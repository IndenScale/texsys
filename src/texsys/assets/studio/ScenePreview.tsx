import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { t } from "./i18n";
import type { WorkbenchProps } from "./sdk";
import { Empty, Icon } from "./ui";
import { sceneFileRequest, sourceReference, type PreviewSource } from "./project-preview-model";
import type { Point3, SceneViewport } from "./preview-viewport";
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

function ObjectTree({
  nodes,
  selected,
  hidden,
  draw,
  expanded,
  toggle,
  select,
  depth = 0,
}: {
  nodes: Map<string, Branch>;
  selected: string;
  hidden: Set<string>;
  expanded: Set<string>;
  draw: boolean;
  toggle: (path: string) => void;
  select: (id: string) => void;
  depth?: number;
}) {
  return [...nodes.values()].map((node) => {
    const branch = node.children.size > 0;
    const open = draw || (depth === 0 ? !expanded.has(node.path) : expanded.has(node.path));
    const object = node.object;
    return (
      <React.Fragment key={node.path}>
        <div className="scene-tree-row" style={{ paddingInlineStart: depth * 11 + 4 }}>
          {branch ? (
            <button
              type="button"
              className="scene-tree-expand"
              aria-expanded={open}
              aria-label={node.label}
              onClick={() => toggle(node.path)}
            >
              {open ? "▾" : "▸"}
            </button>
          ) : (
            <span className="scene-tree-spacer" />
          )}
          <button
            type="button"
            className="scene-tree-name"
            title={object?.semantic_ref ?? node.path}
            data-prim-path={object?.id}
            data-hidden={object && hidden.has(object.id) ? "true" : undefined}
            aria-pressed={object ? object.id === selected : undefined}
            onClick={() => (object ? select(object.id) : toggle(node.path))}
          >
            {object ? <Icon name="cube" size={14} /> : <Icon name="layers" size={14} />}
            <span>{object?.name ?? node.label}</span>
          </button>
        </div>
        {branch && open && (
          <ObjectTree
            {...{ nodes: node.children, selected, hidden, expanded, draw, toggle, select }}
            depth={depth + 1}
          />
        )}
      </React.Fragment>
    );
  });
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
}) {
  const [selected, select] = useState("");
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
  const [expanded, setExpanded] = useState(new Set<string>());
  const [panel, setPanel] = useState<"tree" | "details">("tree");
  const [showPanel, setShowPanel] = useState(true);
  const [flying, setFlying] = useState(false);
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
  const hoveredObject = hover && byId.get(hover.id);
  const source =
    selectedObject && sourceReference(scene, selectedObject.id, selectedObject.semantic_ref ?? "");
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
  const matched = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    return (objects ?? []).filter((object) => {
      if (
        term &&
        ![object.name, object.prim_path, ...Object.values(object.facets ?? {})].some((value) =>
          value.toLocaleLowerCase().includes(term),
        )
      )
        return false;
      return categories.every(
        ([key]) =>
          !filters[key]?.size || valuesFor(object, key).some((value) => filters[key]?.has(value)),
      );
    });
  }, [objects, filters, query]);
  const visible = useMemo(
    () =>
      new Set(
        matched
          .filter((object) => !hidden.has(object.id) && (!isolated || isolated.has(object.id)))
          .map((object) => object.id),
      ),
    [matched, hidden, isolated],
  );
  const tree = useMemo(() => modelTree(matched), [matched]);
  const filtering = Boolean(query.trim() || categories.some(([key]) => filters[key]?.size));

  useEffect(() => {
    select("");
    setHover(undefined);
    setQuery("");
    setFilters({});
    setHidden(new Set());
    setGhost(new Set());
    setIsolated(undefined);
    setExpanded(new Set());
    setAxis(undefined);
    setSection(0.5);
    setPoints([]);
    setMeasuring(false);
    setFlying(false);
  }, [path]);

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
    renderer.current?.setFlying(flying);
  }, [active, canvas, flying]);

  const selectObject = (id: string) => {
    select(id);
    setHover(undefined);
    if (id) {
      setShowPanel(true);
      setPanel("details");
    }
  };
  const restore = () => {
    setHidden(new Set());
    setGhost(new Set());
    setIsolated(undefined);
  };
  const relatedIds = selectedObject?.owner_path
    ? (objects ?? [])
        .filter((object) => object.owner_path === selectedObject.owner_path)
        .map((object) => object.id)
    : selectedObject
      ? [selectedObject.id]
      : [];
  const hideFilter = () => {
    setHidden((old) => new Set([...old, ...visible]));
    setFilters({});
    setQuery("");
    setHover(undefined);
  };
  const geometryBounds = selectedObject?.points.reduce(
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
  const orient = (direction: ViewDirection) => {
    if (flying) {
      renderer.current?.setFlying(false);
      setFlying(false);
    }
    renderer.current?.fit(visible, direction);
  };
  const navigation = [
    ["iso", "等轴测"],
    ["top", "俯视"],
    ["front", "正视"],
    ["side", "侧视"],
  ] as const;

  return (
    <section className="scene-preview">
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
          onChange={(event) => orient(event.target.value as ViewDirection)}
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
        <button
          type="button"
          disabled={!hasObjects}
          aria-pressed={flying}
          onClick={() => {
            setFlying(!flying);
            canvas?.focus();
          }}
        >
          {t("飞行")}
        </button>
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
        <button
          type="button"
          className="scene-panel-toggle"
          aria-pressed={showPanel}
          onClick={() => setShowPanel(!showPanel)}
        >
          <Icon name="layers" size={14} />
          {t("检查面板")}
        </button>
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
              aria-label={t("三维场景；点击对象选择，拖动旋转")}
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
                if (flying && event.button === 2)
                  event.currentTarget.setPointerCapture(event.pointerId);
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
                  if (flying && previous.button === 2) renderer.current?.flyLook(dx, dy);
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
                if (previous?.button === 2 && flying) renderer.current?.finishLook();
                if (!previous || previous.button !== 0 || previous.distance > 4) return;
                const { x, y } = coordinates(event);
                const hit = renderer.current?.pick(x, y);
                if (measuring) {
                  if (hit)
                    setPoints((old) => (old.length === 1 ? [...old, hit.point] : [hit.point]));
                } else selectObject(hit?.id ?? "");
              }}
              onPointerCancel={() => {
                drag.current = undefined;
                setHover(undefined);
              }}
              onPointerLeave={() => setHover(undefined)}
              onBlur={() => renderer.current?.stop()}
              onKeyUp={(event) => {
                if (renderer.current?.key(event.code, false)) event.preventDefault();
              }}
              onKeyDown={(event) => {
                if (event.metaKey || event.ctrlKey || event.altKey) return;
                if (renderer.current?.key(event.code, true)) event.preventDefault();
                else if (!flying && event.key.startsWith("Arrow")) {
                  renderer.current?.orbit(
                    (Number(event.key === "ArrowLeft") - Number(event.key === "ArrowRight")) * 5,
                    (Number(event.key === "ArrowUp") - Number(event.key === "ArrowDown")) * 5,
                  );
                  event.preventDefault();
                } else if (["+", "-", "="].includes(event.key)) {
                  renderer.current?.zoom(event.key === "-" ? 1 / 1.25 : 1.25);
                  event.preventDefault();
                } else if (event.code === "KeyF" && selected && visible.has(selected)) {
                  renderer.current?.fit([selected]);
                  event.preventDefault();
                } else if (event.key === "Home") {
                  renderer.current?.fit();
                  event.preventDefault();
                } else if (event.key === "Escape") {
                  if (measuring) {
                    setMeasuring(false);
                    setPoints([]);
                  } else select("");
                }
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
          {showPanel && (
            <aside className="scene-side" aria-label={t("检查面板")}>
              <div className="scene-side-tabs" role="group" aria-label={t("检查面板")}>
                <button
                  type="button"
                  aria-pressed={panel === "tree"}
                  onClick={() => setPanel("tree")}
                >
                  {t("构件")}
                </button>
                <button
                  type="button"
                  aria-pressed={panel === "details"}
                  onClick={() => setPanel("details")}
                >
                  {t("属性")}
                </button>
              </div>
              {panel === "tree" ? (
                <>
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
                              setQuery("");
                            }}
                          >
                            {t("清除筛选")}
                          </button>
                        )}
                        {filtering && (
                          <button type="button" disabled={!visible.size} onClick={hideFilter}>
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
                  <div className="scene-preview-objects" aria-label={t("场景对象")}>
                    <ObjectTree
                      nodes={tree}
                      selected={selected}
                      hidden={hidden}
                      draw={filtering}
                      expanded={expanded}
                      toggle={(value) => setExpanded((old) => toggleSet(old, value))}
                      select={selectObject}
                    />
                  </div>
                </>
              ) : selectedObject ? (
                <div className="scene-preview-inspector">
                  <div className="scene-selection-title">
                    <span title={selectedObject.prim_path}>{selectedObject.name}</span>
                    <small>
                      {selectedObject.envelope
                        ? t("包络几何")
                        : (selectedObject.type ?? t("网格几何"))}
                    </small>
                  </div>
                  <div className="scene-selection-actions">
                    <button
                      type="button"
                      disabled={!visible.has(selected)}
                      onClick={() => renderer.current?.fit([selected])}
                    >
                      {t("聚焦")}
                    </button>
                    <button
                      type="button"
                      aria-pressed={hidden.has(selected)}
                      onClick={() => setHidden((old) => toggleSet(old, selected))}
                    >
                      {t(hidden.has(selected) ? "显示" : "隐藏")}
                    </button>
                    <button
                      type="button"
                      aria-pressed={ghost.has(selected)}
                      onClick={() => setGhost((old) => toggleSet(old, selected))}
                    >
                      {t("半透明")}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setHidden((old) => new Set([...old].filter((id) => id !== selected)));
                        setIsolated(new Set([selected]));
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
                      <button type="button" onClick={() => openSource(source)} title={source.file}>
                        {t("定位源码")}
                      </button>
                    )}
                  </div>
                  <dl className="scene-properties">
                    <dt>Prim</dt>
                    <dd>{selectedObject.prim_path}</dd>
                    {selectedObject.owner_path &&
                      selectedObject.owner_path !== selectedObject.id && (
                        <>
                          <dt>{t("所属构件")}</dt>
                          <dd>{selectedObject.owner_path}</dd>
                        </>
                      )}
                    {geometryBounds && (
                      <>
                        <dt>{t("包络尺寸")}</dt>
                        <dd>
                          {geometryBounds.max
                            .map((value, index) => (value - geometryBounds.min[index]).toFixed(3))
                            .join(" × ")}{" "}
                          m
                        </dd>
                        <dt>{t("标高范围")}</dt>
                        <dd>
                          {geometryBounds.min[2].toFixed(3)} – {geometryBounds.max[2].toFixed(3)} m
                        </dd>
                      </>
                    )}
                    {categories.flatMap(([key, label]) =>
                      valuesFor(selectedObject, key).map((value) => (
                        <React.Fragment key={key + value}>
                          <dt>{t(label)}</dt>
                          <dd title={value}>{labelFor(key, value)}</dd>
                        </React.Fragment>
                      )),
                    )}
                  </dl>
                  {Object.entries(selectedObject.properties ?? {}).some(
                    ([key]) =>
                      !key.startsWith("texsys:") ||
                      !["identity", ...categories.map(([facet]) => facet)].includes(key.slice(7)),
                  ) && (
                    <details className="scene-authored" open>
                      <summary>{t("USD 属性")}</summary>
                      <dl className="scene-properties">
                        {Object.entries(selectedObject.properties ?? {})
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
            </aside>
          )}
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
          <span>
            {flying
              ? t("WASD 移动，Q/E 升降，右键拖动转向")
              : t("拖动旋转，右键拖动平移，滚轮缩放")}
          </span>
        </footer>
      )}
    </section>
  );
}
