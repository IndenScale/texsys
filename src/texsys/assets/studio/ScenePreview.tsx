import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { t } from "./i18n";
import type { WorkbenchProps } from "./sdk";
import { Empty, Icon } from "./ui";
import { sceneFileRequest, sourceReference, type PreviewSource } from "./project-preview-model";
import {
  defaultSceneInspection,
  type Point3,
  type SceneViewport,
  type SceneViewControls,
} from "./preview-viewport";
import { HeightResizeHandle } from "./atomstudio/ResizeHandle";
import { useUserPreferences } from "./user-preferences";
import {
  SceneRenderer,
  type ModelObject,
  type SceneAxis,
  type ViewDirection,
} from "./scene-renderer";
import { usePreviewSize } from "./use-preview-size";
import "./scene-preview.css";

type Scene = {
  status: "ready";
  path: string;
  version: string;
  revision: string;
  objects: ModelObject[];
  source_map?: Record<string, PreviewSource>;
};
type Response = Scene | { status: "empty"; directory: string } | { status: "unchanged" };

const categories = [
  ["discipline", "专业"],
  ["level", "楼层"],
  ["zone", "分区"],
  ["material", "材料"],
  ["role", "构件类型"],
  ["collection", "集合"],
] as const;
type Category = (typeof categories)[number][0];
type Filters = Partial<Record<Category, Set<string>>>;
const valuesFor = (object: ModelObject, key: Category): string[] =>
  key === "collection"
    ? (object.collections ?? [])
    : object.facets?.[key]
      ? [object.facets[key]]
      : [];
const labelFor = (key: Category, value: string) =>
  key === "collection" ? value.split("collection:").at(-1) : value;
const inspectionNumber = new Intl.NumberFormat("en", {
  maximumSignificantDigits: 9,
  useGrouping: false,
});

type Branch = { path: string; label: string; object?: ModelObject; children: Map<string, Branch> };
function modelTree(objects: ModelObject[]) {
  const roots = new Map<string, Branch>();
  for (const object of objects) {
    let children = roots;
    let path = "";
    for (const name of object.prim_path.split("/").filter(Boolean)) {
      path += "/" + name;
      if (!children.has(name)) children.set(name, { path, label: name, children: new Map() });
      const node = children.get(name)!;
      if (path === object.prim_path) node.object = object;
      children = node.children;
    }
  }
  return roots;
}

type TreeRow = Branch & { parent: string; depth: number; open: boolean };

function treeRows(nodes: Map<string, Branch>, expanded: Map<string, boolean>, searching: boolean) {
  const rows: TreeRow[] = [];
  const add = (branches: Map<string, Branch>, parent: string, depth: number) => {
    for (const node of branches.values()) {
      const open = expanded.get(node.path) ?? (depth === 0 || searching);
      rows.push({ ...node, depth, parent, open });
      if (open) add(node.children, node.path, depth + 1);
    }
  };
  add(nodes, "", 0);
  return rows;
}

function toggleSet(old: Set<string>, key: string) {
  const next = new Set(old);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}

export function ScenePreview({
  api,
  active,
  path,
  version,
  content,
  dirty,
  openSource,
  viewport,
  onViewportChange,
  controls,
}: {
  api: WorkbenchProps["api"];
  active: boolean;
  path: string;
  version: string;
  content: string;
  dirty: boolean;
  openSource?: (source: PreviewSource) => void;
  viewport?: SceneViewport;
  onViewportChange: (viewport: SceneViewport) => void;
  controls?: SceneViewControls;
}) {
  const [standaloneInspection, setStandaloneInspection] = useState(defaultSceneInspection);
  const {
    focused = true,
    focusRequest,
    maximized = false,
    toggleMaximized,
    openSettings,
  } = controls ?? {};
  const inspection = controls
    ? (controls.inspection ?? defaultSceneInspection)
    : standaloneInspection;
  const changeInspection = controls?.changeInspection ?? setStandaloneInspection;
  const { selected, pinned, open: propertiesOpen, propertyShare } = inspection;
  const preferences = useUserPreferences();
  const navigationHint = React.useId();
  const [result, setResult] = useState<{
    key: string;
    value: Exclude<Response, { status: "unchanged" }>;
  }>();
  const [failure, setFailure] = useState<{ key: string; message: string }>();
  const { element: canvas, setElement: setCanvas, size } = usePreviewSize<HTMLCanvasElement>();

  const request = useMemo(
    () => sceneFileRequest(path, version, content, dirty),
    [path, version, content, dirty],
  );
  const current = result?.key === request.key ? result.value : undefined;
  const scene =
    current?.status === "ready" && current.path === path && current.version === version
      ? current
      : undefined;
  const error = failure?.key === request.key ? failure.message : "";
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let revision = "";
    const read = async () => {
      try {
        const url =
          revision && !dirty
            ? `${request.url}&revision=${encodeURIComponent(revision)}`
            : request.url;
        const response = await api<Response>(url, request.body, controller.signal);
        if (controller.signal.aborted) return;
        if (response.status === "ready" && (response.path !== path || response.version !== version))
          throw new Error(t("场景响应与当前文件不匹配"));
        setFailure(undefined);
        if (response.status !== "unchanged") {
          revision = response.status === "ready" ? response.revision : "";
          setResult({ key: request.key, value: response });
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        revision = "";
        setResult(undefined);
        setFailure({ key: request.key, message: String(error) });
      } finally {
        // A clean USD root can keep its bytes while its composition layers change.
        if (!controller.signal.aborted && !dirty) timer = setTimeout(() => void read(), 2000);
      }
    };
    void read();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [active, api, request, path, version, dirty]);
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<Filters>({});
  const [hidden, setHidden] = useState(new Set<string>());
  const [ghost, setGhost] = useState(new Set<string>());
  const [isolated, setIsolated] = useState<Set<string>>();
  const [expanded, setExpanded] = useState(new Map<string, boolean>());
  const [treeFocus, setTreeFocus] = useState("");
  const treeElement = useRef<HTMLDivElement>(null);
  const { setElement: setSidebar, size: sidebarSize } = usePreviewSize<HTMLElement>();
  const propertyPanel = useRef<HTMLDivElement>(null);
  const propertySpace = Math.max(0, sidebarSize.height - 5);
  const propertyHeight = Math.round((propertySpace * propertyShare) / 100);
  const [hover, setHover] = useState<{ id: string; x: number; y: number }>();
  const [axis, setAxis] = useState<SceneAxis>();
  const [section, setSection] = useState(0.5);
  const [measuring, setMeasuring] = useState(false);
  const [points, setPoints] = useState<Point3[]>([]);
  const [renderError, setRenderError] = useState("");
  const renderer = useRef<SceneRenderer | null>(null);
  const viewportRef = useRef(viewport);
  viewportRef.current = viewport;
  const changeRef = useRef(onViewportChange);
  changeRef.current = onViewportChange;
  const drag = useRef<{ x: number; y: number; button: number; distance: number } | undefined>(
    undefined,
  );
  const lastHover = useRef(0);
  const objects = scene?.objects;
  const hasObjects = Boolean(objects?.length);
  const byId = useMemo(() => new Map(objects?.map((object) => [object.id, object])), [objects]);
  const selectedObject = byId.get(selected);
  const inspected = byId.get(pinned || selected);
  const hoveredObject = hover && byId.get(hover.id);
  const source = inspected && sourceReference(scene, inspected.id, inspected.semantic_ref ?? "");
  const filterOptions = useMemo(
    () =>
      categories
        .map(([key, label]) => ({
          key,
          label,
          options: [...new Set(objects?.flatMap((object) => valuesFor(object, key)))].sort((a, b) =>
            a.localeCompare(b),
          ),
        }))
        .filter(({ options }) => options.length),
    [objects],
  );
  const matched = useMemo(
    () =>
      (objects ?? []).filter((object) =>
        categories.every(
          ([key]) =>
            !filters[key]?.size || valuesFor(object, key).some((value) => filters[key]?.has(value)),
        ),
      ),
    [objects, filters],
  );
  const visible = useMemo(
    () =>
      new Set(
        (isolated ? (objects ?? []) : matched)
          .filter((object) => !hidden.has(object.id) && (!isolated || isolated.has(object.id)))
          .map((object) => object.id),
      ),
    [matched, objects, hidden, isolated],
  );
  const tree = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    return modelTree(
      term
        ? matched.filter((object) =>
            [object.name, object.prim_path, ...Object.values(object.facets ?? {})].some((value) =>
              value.toLocaleLowerCase().includes(term),
            ),
          )
        : matched,
    );
  }, [matched, query]);
  const rows = useMemo(() => treeRows(tree, expanded, !!query.trim()), [tree, expanded, query]);
  const treeTabStop = rows.find((row) => row.path === treeFocus)?.path ?? rows[0]?.path;
  const filtering = categories.some(([key]) => filters[key]?.size);

  const focusViewport = () => canvas?.focus({ preventScroll: true });
  const lastFocus = useRef<SceneViewControls["focusRequest"]>(undefined);
  useLayoutEffect(() => {
    if (!canvas || !active || !focused || lastFocus.current === focusRequest) return;
    lastFocus.current = focusRequest;
    if (
      focusRequest &&
      (focusRequest.origin === undefined ||
        document.activeElement === focusRequest.origin ||
        document.activeElement === document.body ||
        !focusRequest.origin?.isConnected)
    )
      canvas.focus({ preventScroll: true });
  }, [active, canvas, focused, focusRequest]);

  useLayoutEffect(() => {
    if (!canvas || !active) return;
    let instance: SceneRenderer;
    try {
      instance = new SceneRenderer(canvas, (state) => changeRef.current(state));
      setRenderError("");
    } catch {
      setRenderError(t("无法启动 WebGL 预览。请检查浏览器的图形加速设置。"));
      return;
    }
    renderer.current = instance;
    return () => {
      instance.dispose();
      renderer.current = null;
    };
  }, [active, canvas]);

  useLayoutEffect(() => {
    if (active) renderer.current?.resize(size.width, size.height);
  }, [active, canvas, size.width, size.height]);
  useLayoutEffect(() => {
    if (objects?.length) renderer.current?.load(objects, viewportRef.current);
  }, [active, canvas, objects]);
  useLayoutEffect(() => {
    renderer.current?.setState({ visible, ghost, selected, hovered: hover?.id ?? "" });
  }, [active, canvas, visible, ghost, selected, hover?.id]);
  useLayoutEffect(() => {
    renderer.current?.setSection(axis, section);
  }, [active, canvas, objects, axis, section]);
  useLayoutEffect(() => {
    renderer.current?.measurement(points);
  }, [active, canvas, objects, points]);
  useLayoutEffect(() => {
    renderer.current?.setNavigation(preferences);
  }, [active, canvas, preferences]);
  useLayoutEffect(() => {
    if (!focused) renderer.current?.stop();
  }, [focused]);
  useEffect(() => {
    const stop = () => renderer.current?.stop();
    const visibility = () => {
      if (document.hidden) stop();
    };
    window.addEventListener("blur", stop);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      window.removeEventListener("blur", stop);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, []);

  const selectObject = (id: string, openProperties = false) => {
    changeInspection({ ...inspection, selected: id, open: propertiesOpen || openProperties });
    setHover(undefined);
  };
  const pickObject = (id: string) => {
    selectObject(id);
    if (!id) return;
    setTreeFocus(id);
    setExpanded((previous) => {
      const next = new Map(previous);
      for (
        let parent = id.slice(0, id.lastIndexOf("/"));
        parent;
        parent = parent.slice(0, parent.lastIndexOf("/"))
      )
        next.set(parent, true);
      return next;
    });
  };
  useLayoutEffect(() => {
    if (selected && treeElement.current && document.activeElement === canvas)
      treeElement.current
        .querySelector('[data-prim-path="' + CSS.escape(selected) + '"]')
        ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [selected, rows, canvas]);
  const changeBranch = (row: TreeRow) =>
    setExpanded((previous) => new Map(previous).set(row.path, !row.open));
  const focusRow = (path: string | undefined) => {
    if (path)
      treeElement.current
        ?.querySelector<HTMLElement>('[data-tree-path="' + CSS.escape(path) + '"]')
        ?.focus();
  };
  const closeProperties = () => {
    changeInspection({ ...inspection, open: false, pinned: "" });
    focusViewport();
  };
  const restore = () => {
    setHidden(new Set());
    setGhost(new Set());
    setIsolated(undefined);
  };
  const relatedIds = inspected?.owner_path
    ? (objects ?? [])
        .filter((object) => object.owner_path === inspected.owner_path)
        .map((object) => object.id)
    : inspected
      ? [inspected.id]
      : [];
  const hideFilter = () => {
    setHidden((old) => new Set([...old, ...matched.map((object) => object.id)]));
    setFilters({});
    setHover(undefined);
  };
  const geometryBounds = inspected?.points.reduce(
    (result, point) => {
      point.forEach((value, i) => {
        result.min[i] = Math.min(result.min[i], value);
        result.max[i] = Math.max(result.max[i], value);
      });
      return result;
    },
    { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] },
  );
  const distance =
    points.length === 2
      ? Math.hypot(...points[0].map((value, i) => points[1][i] - value))
      : undefined;
  const coordinates = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
      width: rect.width,
      height: rect.height,
    };
  };
  const navigation = [
    ["iso", "等轴测"],
    ["top", "俯视"],
    ["front", "正视"],
    ["side", "侧视"],
  ] as const;

  return (
    <section
      className="scene-preview"
      onKeyDown={(event) => {
        if (event.defaultPrevented || event.nativeEvent.isComposing || event.keyCode === 229)
          return;
        if (event.key === "Escape") {
          const target = event.target as HTMLElement;
          if (target.closest("input, select, textarea, [contenteditable]") && !maximized) return;
          if (measuring) {
            setMeasuring(false);
            setPoints([]);
          } else if (maximized) toggleMaximized?.();
          else if (target !== canvas) focusViewport();
          else return;
          event.preventDefault();
          event.stopPropagation();
        }
      }}
    >
      <div className="scene-preview-controls" role="toolbar" aria-label={t("场景视角")}>
        <button
          type="button"
          disabled={!hasObjects}
          onClick={() => renderer.current?.zoom(1 / 1.25)}
          aria-label={t("缩小")}
        >
          −
        </button>
        <button
          type="button"
          disabled={!hasObjects}
          onClick={() => renderer.current?.zoom(1.25)}
          aria-label={t("放大")}
        >
          +
        </button>
        <button type="button" disabled={!visible.size} onClick={() => renderer.current?.fit()}>
          {t("适应视图")}
        </button>
        <select
          aria-label={t("标准视图")}
          value=""
          disabled={!visible.size}
          onChange={(event) => {
            renderer.current?.fit(visible, event.target.value as ViewDirection);
            focusViewport();
          }}
        >
          <option value="" disabled>
            {t("标准视图")}
          </option>
          {navigation.map(([value, label]) => (
            <option key={value} value={value}>
              {t(label)}
            </option>
          ))}
        </select>
        <span className="scene-control-divider" />
        <select
          aria-label={t("剖切")}
          value={axis ?? ""}
          disabled={!hasObjects}
          onChange={(event) => setAxis((event.target.value || undefined) as SceneAxis | undefined)}
        >
          <option value="">{t("剖切")}</option>
          <option value="x">X</option>
          <option value="y">Y</option>
          <option value="z">Z</option>
        </select>
        {axis && (
          <input
            aria-label={t("剖切位置")}
            className="scene-section-range"
            type="range"
            min={0}
            max={1}
            step={0.005}
            value={section}
            onChange={(event) => setSection(Number(event.target.value))}
          />
        )}
        <button
          type="button"
          disabled={!hasObjects}
          aria-pressed={measuring}
          onClick={() => {
            setPoints([]);
            setMeasuring(!measuring);
            canvas?.focus();
          }}
        >
          {t("测距")}
        </button>
        <span className="scene-toolbar-end" />
        <button
          type="button"
          disabled={!selectedObject}
          onClick={() => selectObject(selected, true)}
        >
          {t("查看属性")}
        </button>
        {openSettings && (
          <button
            type="button"
            title={t("打开 USD 预览设置")}
            aria-label={t("打开 USD 预览设置")}
            onClick={openSettings}
          >
            <Icon name="settings" size={14} />
          </button>
        )}
        {toggleMaximized && (
          <button type="button" aria-keyshortcuts="Shift+F" onClick={toggleMaximized}>
            <Icon name={maximized ? "panel-restore" : "panel-maximize"} size={14} />
            {t(maximized ? "退出场景全屏" : "场景全屏")}
          </button>
        )}
      </div>
      {(error || renderError) && (
        <div className="scene-preview-error" role="alert">
          {error || renderError}
        </div>
      )}
      {hasObjects ? (
        <div className="scene-preview-body">
          <div className="scene-viewport">
            <canvas
              ref={setCanvas}
              className="scene-preview-canvas"
              aria-label={t("三维场景")}
              aria-describedby={navigationHint}
              aria-keyshortcuts="W A S D Q E ArrowUp ArrowDown ArrowLeft ArrowRight F Home Enter Shift+F"
              tabIndex={0}
              onContextMenu={(event) => event.preventDefault()}
              onPointerDown={(event) => {
                event.currentTarget.focus({ preventScroll: true });
                drag.current = {
                  x: event.clientX,
                  y: event.clientY,
                  button: event.button,
                  distance: 0,
                };
                setHover(undefined);
              }}
              onPointerMove={(event) => {
                const previous = drag.current;
                if (previous && event.buttons) {
                  const dx = event.clientX - previous.x,
                    dy = event.clientY - previous.y;
                  drag.current = {
                    ...previous,
                    x: event.clientX,
                    y: event.clientY,
                    distance: previous.distance + Math.hypot(dx, dy),
                  };
                  return;
                }
                if (event.timeStamp - lastHover.current < 34) return;
                lastHover.current = event.timeStamp;
                const { x, y, width, height } = coordinates(event);
                const result = renderer.current?.pick(x, y);
                setHover(
                  result
                    ? {
                        id: result.id,
                        x: Math.max(6, Math.min(x + 14, width - 198)),
                        y: Math.min(y + 14, height - 42),
                      }
                    : undefined,
                );
              }}
              onPointerUp={(event) => {
                const previous = drag.current;
                drag.current = undefined;
                if (event.currentTarget.hasPointerCapture(event.pointerId))
                  event.currentTarget.releasePointerCapture(event.pointerId);
                if (!previous || previous.button !== 0 || previous.distance > 4) return;
                const { x, y } = coordinates(event);
                const hit = renderer.current?.pick(x, y);
                if (measuring) {
                  if (hit)
                    setPoints((old) => (old.length === 1 ? [...old, hit.point] : [hit.point]));
                } else pickObject(hit?.id ?? "");
              }}
              onPointerCancel={() => {
                drag.current = undefined;
                setHover(undefined);
              }}
              onPointerLeave={() => setHover(undefined)}
              onBlur={() => renderer.current?.stop()}
              onKeyUp={(event) => {
                if (renderer.current?.key(event.code, false, event.shiftKey))
                  event.preventDefault();
              }}
              onKeyDown={(event) => {
                if (
                  event.metaKey ||
                  event.ctrlKey ||
                  event.altKey ||
                  event.nativeEvent.isComposing ||
                  event.keyCode === 229
                )
                  return;
                if (renderer.current?.key(event.code, true, event.shiftKey)) {
                  event.preventDefault();
                  return;
                }
                if (event.code === "KeyF" && event.shiftKey && toggleMaximized) {
                  if (!event.repeat) toggleMaximized();
                } else if (["+", "-", "="].includes(event.key)) {
                  renderer.current?.zoom(event.key === "-" ? 1 / 1.25 : 1.25);
                } else if (event.code === "KeyF" && selected && visible.has(selected)) {
                  renderer.current?.fit([selected]);
                } else if (event.key === "Home") {
                  renderer.current?.fit();
                } else if (event.key === "Enter" && selectedObject) {
                  selectObject(selected, true);
                } else return;
                event.preventDefault();
              }}
            />
            {hover && hoveredObject && (
              <div className="scene-hover" style={{ left: hover.x, top: hover.y }}>
                <span>{hoveredObject.name}</span>
                <small>
                  {hoveredObject.facets?.role ??
                    hoveredObject.facets?.material ??
                    hoveredObject.type}
                </small>
              </div>
            )}
            {!visible.size && <div className="scene-view-message">{t("当前没有可见构件")}</div>}
            {measuring && (
              <div className="scene-view-message">
                {distance === undefined
                  ? t(points.length ? "选择第二个点" : "选择两个构件表面上的点")
                  : `${distance.toFixed(3)} m`}
              </div>
            )}
          </div>
          <aside ref={setSidebar} className="scene-side" aria-label={t("构件检查")}>
            <div className="scene-tree-panel">
              <div className="scene-panel-heading">{t("构件")}</div>
              <div className="scene-filters">
                <input
                  type="search"
                  placeholder={t("搜索构件")}
                  aria-label={t("搜索构件")}
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
                {filterOptions.map(({ key, label, options }) => (
                  <details key={key} className="scene-filter">
                    <summary>
                      {t(label)}
                      {filters[key]?.size ? ` (${filters[key]?.size})` : ""}
                    </summary>
                    <div>
                      {options.map((value) => (
                        <label key={value} title={value}>
                          <input
                            type="checkbox"
                            checked={filters[key]?.has(value) ?? false}
                            onChange={() =>
                              setFilters((old) => ({
                                ...old,
                                [key]: toggleSet(old[key] ?? new Set(), value),
                              }))
                            }
                          />
                          <span>{labelFor(key, value)}</span>
                        </label>
                      ))}
                    </div>
                  </details>
                ))}
                {(filtering || hidden.size > 0 || isolated || ghost.size > 0) && (
                  <div className="scene-filter-actions">
                    {filtering && (
                      <button
                        type="button"
                        onClick={() => {
                          setFilters({});
                        }}
                      >
                        {t("清除筛选")}
                      </button>
                    )}
                    {filtering && (
                      <button
                        type="button"
                        disabled={!matched.some((object) => !hidden.has(object.id))}
                        onClick={hideFilter}
                      >
                        {t("隐藏筛选结果")}
                      </button>
                    )}
                    {(hidden.size > 0 || isolated || ghost.size > 0) && (
                      <button type="button" onClick={restore}>
                        {t("恢复显隐")}
                      </button>
                    )}
                  </div>
                )}
              </div>
              <div
                ref={treeElement}
                className="scene-preview-objects"
                role="tree"
                aria-label={t("场景对象")}
                onKeyDown={(event) => {
                  if (
                    event.altKey ||
                    event.ctrlKey ||
                    event.metaKey ||
                    event.nativeEvent.isComposing
                  )
                    return;
                  const path = (event.target as HTMLElement).dataset.treePath;
                  const index = rows.findIndex((row) => row.path === path);
                  const row = rows[index];
                  if (!row) return;
                  switch (event.key) {
                    case "ArrowDown":
                      focusRow(rows[index + 1]?.path);
                      break;
                    case "ArrowUp":
                      focusRow(rows[index - 1]?.path);
                      break;
                    case "Home":
                      focusRow(rows[0]?.path);
                      break;
                    case "End":
                      focusRow(rows.at(-1)?.path);
                      break;
                    case "ArrowRight":
                      if (row.children.size && !row.open) changeBranch(row);
                      else if (rows[index + 1]?.parent === row.path) focusRow(rows[index + 1].path);
                      break;
                    case "ArrowLeft":
                      if (row.children.size && row.open) changeBranch(row);
                      else focusRow(row.parent);
                      break;
                    case "Enter":
                    case " ":
                      if (row.object) selectObject(row.object.id, event.key === "Enter");
                      else if (row.children.size) changeBranch(row);
                      break;
                    default:
                      return;
                  }
                  event.preventDefault();
                }}
              >
                {rows.map((row) => {
                  const { path, object, depth, children, open } = row;
                  return (
                    <div
                      key={path}
                      role="treeitem"
                      aria-level={depth + 1}
                      aria-expanded={children.size ? open : undefined}
                      aria-selected={object ? selected === object.id : undefined}
                      className="scene-tree-row"
                      data-tree-path={path}
                      data-prim-path={object?.id}
                      data-hidden={object && !visible.has(object.id) ? "true" : undefined}
                      tabIndex={treeTabStop === path ? 0 : -1}
                      style={{ paddingInlineStart: depth * 11 + 4 }}
                      title={object?.semantic_ref ?? path}
                      onFocus={() => setTreeFocus(path)}
                      onClick={(event) => {
                        event.currentTarget.focus();
                        if (object) selectObject(object.id);
                        else if (children.size) changeBranch(row);
                      }}
                    >
                      <span
                        aria-hidden="true"
                        className="scene-tree-expand"
                        onClick={
                          children.size
                            ? (event) => {
                                event.stopPropagation();
                                focusRow(path);
                                changeBranch(row);
                              }
                            : undefined
                        }
                      >
                        {children.size ? (open ? "▾" : "▸") : ""}
                      </span>
                      <Icon name={object ? "cube" : "layers"} size={14} />
                      <span className="scene-tree-label">{object?.name ?? row.label}</span>
                    </div>
                  );
                })}
                {!rows.length && <div className="scene-no-selection">{t("没有匹配的构件")}</div>}
              </div>
            </div>
            {propertiesOpen && (
              <>
                <HeightResizeHandle
                  className="scene-properties-divider"
                  label={t("调整构件属性高度")}
                  panelRef={propertyPanel}
                  previewProperty="--scene-property-preview-height"
                  value={propertyHeight}
                  min={Math.round(propertySpace * 0.23)}
                  max={Math.round(propertySpace * 0.72)}
                  defaultValue={Math.round(
                    (propertySpace * defaultSceneInspection.propertyShare) / 100,
                  )}
                  grows="up"
                  mode="hide"
                  collapsible={false}
                  open
                  onClose={closeProperties}
                  onChange={(height) => {
                    if (propertySpace)
                      changeInspection({
                        ...inspection,
                        propertyShare: (height * 100) / propertySpace,
                      });
                  }}
                />
                <div
                  ref={propertyPanel}
                  className="scene-property-panel"
                  style={
                    {
                      "--scene-property-height": propertySpace
                        ? propertyHeight + "px"
                        : propertyShare + "%",
                    } as React.CSSProperties
                  }
                >
                  <div className="scene-panel-heading">
                    <span>{t(pinned ? "已固定属性" : "属性")}</span>
                    <button
                      type="button"
                      aria-pressed={!!pinned}
                      disabled={!pinned && !inspected}
                      aria-label={t(pinned ? "取消固定属性" : "固定构件属性")}
                      title={t(pinned ? "取消固定属性" : "固定构件属性")}
                      onClick={() =>
                        changeInspection({
                          ...inspection,
                          pinned: pinned ? "" : (inspected?.id ?? ""),
                        })
                      }
                    >
                      <Icon name="pin" size={14} />
                    </button>
                    <button
                      type="button"
                      aria-label={t("关闭构件属性")}
                      title={t("关闭构件属性")}
                      onClick={closeProperties}
                    >
                      <Icon name="close" size={14} />
                    </button>
                  </div>
                  {inspected ? (
                    <div className="scene-preview-inspector">
                      <div className="scene-selection-title">
                        <span title={inspected.prim_path}>{inspected.name}</span>
                        <small>
                          {inspected.envelope ? t("包络几何") : (inspected.type ?? t("网格几何"))}
                        </small>
                      </div>
                      <div className="scene-selection-actions">
                        <button
                          type="button"
                          disabled={!visible.has(inspected.id)}
                          onClick={() => renderer.current?.fit([inspected.id])}
                        >
                          {t("聚焦")}
                        </button>
                        <button
                          type="button"
                          aria-pressed={hidden.has(inspected.id)}
                          onClick={() => setHidden((old) => toggleSet(old, inspected.id))}
                        >
                          {t(hidden.has(inspected.id) ? "显示" : "隐藏")}
                        </button>
                        <button
                          type="button"
                          aria-pressed={ghost.has(inspected.id)}
                          onClick={() => setGhost((old) => toggleSet(old, inspected.id))}
                        >
                          {t("半透明")}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setHidden(
                              (old) => new Set([...old].filter((id) => id !== inspected.id)),
                            );
                            setIsolated(new Set([inspected.id]));
                          }}
                        >
                          {t("仅显示部件")}
                        </button>
                        {relatedIds.length > 1 && (
                          <button
                            type="button"
                            onClick={() => {
                              setHidden(
                                (old) => new Set([...old].filter((id) => !relatedIds.includes(id))),
                              );
                              setIsolated(new Set(relatedIds));
                            }}
                          >
                            {t("仅显示构件")}
                          </button>
                        )}
                        {(hidden.size > 0 || isolated || ghost.size > 0) && (
                          <button type="button" onClick={restore}>
                            {t("恢复显隐")}
                          </button>
                        )}
                        {source && openSource && (
                          <button
                            type="button"
                            onClick={() => openSource(source)}
                            title={source.file}
                          >
                            {t("定位源码")}
                          </button>
                        )}
                      </div>
                      <dl className="scene-properties">
                        <dt>Prim</dt>
                        <dd>{inspected.prim_path}</dd>
                        {inspected.owner_path && inspected.owner_path !== inspected.id && (
                          <>
                            <dt>{t("所属构件")}</dt>
                            <dd>{inspected.owner_path}</dd>
                          </>
                        )}
                        {geometryBounds && (
                          <>
                            <dt>{t("包络尺寸")}</dt>
                            <dd>
                              {geometryBounds.max
                                .map((value, index) =>
                                  (value - geometryBounds.min[index]).toFixed(3),
                                )
                                .join(" × ")}{" "}
                              m
                            </dd>
                            <dt>{t("标高范围")}</dt>
                            <dd>
                              {geometryBounds.min[2].toFixed(3)} –{" "}
                              {geometryBounds.max[2].toFixed(3)} m
                            </dd>
                          </>
                        )}
                        {categories.flatMap(([key, label]) =>
                          valuesFor(inspected, key).map((value) => (
                            <React.Fragment key={key + value}>
                              <dt>{t(label)}</dt>
                              <dd title={value}>{labelFor(key, value)}</dd>
                            </React.Fragment>
                          )),
                        )}
                      </dl>
                      {Object.entries(inspected.properties ?? {}).some(
                        ([key]) =>
                          !key.startsWith("texsys:") ||
                          !["identity", ...categories.map(([facet]) => facet)].includes(
                            key.slice(7),
                          ),
                      ) && (
                        <details className="scene-authored" open>
                          <summary>{t("USD 属性")}</summary>
                          <dl className="scene-properties">
                            {Object.entries(inspected.properties ?? {})
                              .filter(
                                ([key]) =>
                                  !key.startsWith("texsys:") ||
                                  !["identity", ...categories.map(([facet]) => facet)].includes(
                                    key.slice(7),
                                  ),
                              )
                              .map(([key, row]) => (
                                <React.Fragment key={key}>
                                  <dt>{key}</dt>
                                  <dd title={`${row.prim}\n${String(row.value)}`}>
                                    {typeof row.value === "number"
                                      ? inspectionNumber.format(row.value)
                                      : String(row.value)}
                                    {row.unit ? " " + row.unit : ""}
                                  </dd>
                                </React.Fragment>
                              ))}
                          </dl>
                        </details>
                      )}
                    </div>
                  ) : (
                    <div className="scene-no-selection">{t("选择构件以查看属性")}</div>
                  )}
                </div>
              </>
            )}
          </aside>
        </div>
      ) : (
        <Empty
          title={t(error ? "无法预览此文件" : current ? "此场景没有可预览的几何" : "正在读取场景")}
        >
          {current && t("预览支持 USD 网格、立方体和 BRep 包络。")}
        </Empty>
      )}
      {hasObjects && (
        <footer className="scene-preview-footer">
          <span>{t("{0} / {1} 个对象可见", { "0": visible.size, "1": objects?.length ?? 0 })}</span>
          <span id={navigationHint}>
            {t("WASD 移动 · Q/E 升降 · 方向键转向 · 左键旋转 / 右键平移")}
          </span>
        </footer>
      )}
    </section>
  );
}
