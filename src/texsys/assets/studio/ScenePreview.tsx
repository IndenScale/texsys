import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
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
import { EditorActionButton } from "./EditorActionButton";
import { EditorControlSlot } from "./EditorControlSlot";
import { SceneCameraControls } from "./SceneCameraControls";
import { NavigatorMenu } from "./NavigatorMenu";
import { useUserPreferences } from "./user-preferences";
import { SceneRenderer, type ModelObject, type SceneAxis } from "./scene-renderer";
import { usePreviewSize } from "./use-preview-size";
import "./scene-preview.css";

type Scene = {
  status: "ready";
  path: string;
  version: string;
  revision: string;
  objects: ModelObject[];
  source_map?: Record<string, PreviewSource>;
  properties_deferred?: true;
};
type Response = Scene | { status: "empty"; directory: string } | { status: "unchanged" };
type AuthoredProperties = NonNullable<ModelObject["properties"]>;
type PropertyResponse = Pick<Scene, "path" | "version" | "revision"> & {
  properties: Record<string, AuthoredProperties>;
};
const sceneBuffers = new WeakMap<
  WorkbenchProps["api"],
  Map<string, { scene: Scene; weight: number }>
>();
const sceneBufferBudget = 32 * 1024 * 1024;

function rememberScene(
  cache: Map<string, { scene: Scene; weight: number }>,
  key: string,
  scene: Scene,
) {
  const weight = scene.objects.reduce(
    (total, object) =>
      total +
      1024 +
      128 * object.points.length +
      96 * object.faces.length +
      256 * Object.keys(object.properties ?? {}).length,
    0,
  );
  cache.delete(key);
  if (weight > sceneBufferBudget) return;
  cache.set(key, { scene, weight });
  let used = [...cache.values()].reduce((size, item) => size + item.weight, 0);
  for (const [oldest, entry] of cache) {
    if (cache.size <= 4 && used <= sceneBufferBudget) break;
    cache.delete(oldest);
    used -= entry.weight;
  }
}

const categories = [
  ["discipline", "专业"],
  ["level", "楼层"],
  ["zone", "分区"],
  ["material", "材料"],
  ["role", "构件类型"],
  ["collection", "集合"],
] as const;
const implicitProperties = new Set(["identity", ...categories.map(([facet]) => facet)]);
type Category = (typeof categories)[number][0];
type Filters = Partial<Record<Category, Set<string>>>;
type ViewportPointer = { x: number; y: number };
const hoverDelay = 300;
const treeRowHeight = 24;
const treeOverscan = 10;
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
  projectPath = "",
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
  projectPath?: string;
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
  const { focused = true, focusRequest, maximized = false, toggleMaximized } = controls ?? {};
  const inspection = controls
    ? (controls.inspection ?? defaultSceneInspection)
    : standaloneInspection;
  const changeInspection = controls?.changeInspection ?? setStandaloneInspection;
  const { selected, following, pinned } = inspection;
  const preferences = useUserPreferences();
  const navigationHint = React.useId();
  const [keyboardFocus, setKeyboardFocus] = useState(false);
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
    let cache = sceneBuffers.get(api);
    if (!cache) {
      cache = new Map();
      sceneBuffers.set(api, cache);
    }
    const saved = dirty ? undefined : cache.get(request.key);
    if (saved) {
      cache.delete(request.key);
      cache.set(request.key, saved);
    }
    let loaded = false;
    let pending = false;
    let revision = saved?.scene.revision ?? "";
    const pageVisible = () => document.visibilityState !== "hidden";
    const read = async () => {
      if (pending || !pageVisible() || controller.signal.aborted) return;
      pending = true;
      try {
        const url = dirty
          ? request.url
          : `${request.url}&view=geometry${revision ? `&revision=${encodeURIComponent(revision)}` : ""}`;
        const response = await api<Response>(url, request.body, controller.signal);
        if (controller.signal.aborted) return;
        if (response.status === "ready" && (response.path !== path || response.version !== version))
          throw new Error(t("场景响应与当前文件不匹配"));
        setFailure(undefined);
        if (response.status === "unchanged") {
          if (!loaded && saved) setResult({ key: request.key, value: saved.scene });
        } else {
          revision = response.status === "ready" ? response.revision : "";
          if (!dirty && response.status === "ready") rememberScene(cache, request.key, response);
          else cache.delete(request.key);
          setResult({ key: request.key, value: response });
        }
        loaded = true;
      } catch (error) {
        if (controller.signal.aborted) return;
        revision = "";
        cache.delete(request.key);
        setResult(undefined);
        setFailure({ key: request.key, message: String(error) });
      } finally {
        pending = false;
        // Check composed layers even when the root document stays unchanged.
        if (!controller.signal.aborted && !dirty && pageVisible())
          timer = setTimeout(() => void read(), 2000);
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") clearTimeout(timer);
      else if (!dirty || !loaded) {
        clearTimeout(timer);
        void read();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    void read();
    return () => {
      controller.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [active, api, request, path, version, dirty]);
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<Filters>({});
  const [hidden, setHidden] = useState(new Set<string>());
  const [ghost, setGhost] = useState(new Set<string>());
  const [isolated, setIsolated] = useState<Set<string>>();
  const [expanded, setExpanded] = useState(new Map<string, boolean>());
  const [treeFocus, setTreeFocus] = useState("");
  const {
    element: treeElement,
    setElement: setTreeElement,
    size: treeSize,
  } = usePreviewSize<HTMLDivElement>();
  const [treeScroll, setTreeScroll] = useState(0);
  const pendingTreeFocus = useRef<string | undefined>(undefined);
  const [hover, setHover] = useState<ViewportPointer & { id: string }>();
  const [previewedId, setPreviewedId] = useState<string>();
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
  const hoverFrame = useRef<number | undefined>(undefined);
  const pointer = useRef<ViewportPointer | undefined>(undefined);
  const hoverTarget = useRef<string | undefined>(undefined);
  const clearHover = useCallback(() => {
    if (hoverFrame.current !== undefined) cancelAnimationFrame(hoverFrame.current);
    hoverFrame.current = undefined;
    pointer.current = undefined;
    hoverTarget.current = undefined;
    setHover(undefined);
    setPreviewedId(undefined);
  }, []);
  const objects = scene?.objects;
  const hasObjects = Boolean(objects?.length);
  const byId = useMemo(() => new Map(objects?.map((object) => [object.id, object])), [objects]);
  const selection = useMemo(() => new Set(selected), [selected]);
  const cardGroups = [
    { kind: "pinned", label: "固定的属性", ids: pinned },
    { kind: "selection", label: "当前选择的属性", ids: following },
  ]
    .map(({ ids, ...group }) => ({
      ...group,
      cards: ids.map((id) => byId.get(id)).filter((object): object is ModelObject => !!object),
    }))
    .filter(({ cards }) => cards.length > 0);
  const propertyScene = scene?.properties_deferred ? scene : undefined;
  const propertyKey = propertyScene
    ? JSON.stringify([propertyScene.path, propertyScene.version, propertyScene.revision])
    : undefined;
  const [propertyResult, setPropertyResult] = useState<{
    key: string;
    values: Map<string, AuthoredProperties | null>;
  }>();
  const properties = propertyResult?.key === propertyKey ? propertyResult?.values : undefined;
  const inspectedIds = useMemo(
    () => [...new Set([...following, ...pinned])].filter((id) => byId.has(id)),
    [following, pinned, byId],
  );
  const pendingProperties = propertyKey
    ? JSON.stringify(inspectedIds.filter((id) => !properties?.has(id)).slice(0, 64))
    : "[]";
  useEffect(() => {
    if (!active || !propertyScene || !propertyKey || pendingProperties === "[]") return;
    const { path, version, revision } = propertyScene;
    const ids = JSON.parse(pendingProperties) as string[];
    const controller = new AbortController();
    const store = (rows: Record<string, AuthoredProperties> | null) => {
      if (controller.signal.aborted) return;
      setPropertyResult((previous) => {
        const values = new Map(previous?.key === propertyKey ? previous.values : undefined);
        for (const id of ids) values.set(id, rows?.[id] ?? null);
        if (values.size > 256) {
          const keep = new Set(inspectedIds);
          for (const id of values.keys()) {
            if (values.size <= 256) break;
            if (!keep.has(id)) values.delete(id);
          }
        }
        return { key: propertyKey, values };
      });
    };
    void api<PropertyResponse>(
      "/api/scene/properties",
      { path, version, revision, ids },
      controller.signal,
    )
      .then((response) => {
        if (
          response.path === path &&
          response.version === version &&
          response.revision === revision
        )
          store(response.properties);
        else store(null);
      })
      .catch(() => store(null));
    return () => controller.abort();
  }, [active, api, inspectedIds, pendingProperties, propertyKey, propertyScene]);
  const retryProperties = (id: string) => {
    setPropertyResult((previous) => {
      if (!previous || previous.key !== propertyKey) return previous;
      const values = new Map(previous.values);
      values.delete(id);
      return { key: previous.key, values };
    });
  };
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
  const rowIndexes = useMemo(() => new Map(rows.map((row, index) => [row.path, index])), [rows]);
  const focusIndex = rowIndexes.get(treeFocus) ?? 0;
  const treeTabStop = rows[focusIndex]?.path;
  const windowed = rows.length > 200;
  const start = windowed
    ? Math.max(0, Math.min(rows.length - 1, Math.floor(treeScroll / treeRowHeight)) - treeOverscan)
    : 0;
  const end = windowed
    ? Math.min(rows.length, start + Math.ceil(treeSize.height / treeRowHeight) + treeOverscan * 2)
    : rows.length;
  const shownRows = rows.slice(start, end);
  if (windowed && (focusIndex < start || focusIndex >= end)) shownRows.push(rows[focusIndex]);
  const filtering = categories.some(([key]) => filters[key]?.size);
  const hoverId = hover && visible.has(hover.id) ? hover.id : undefined;
  const hoveredObject = hoverId && hoverId === previewedId ? byId.get(hoverId) : undefined;
  const hoverFields = hoveredObject
    ? [
        ...new Set(
          ["role", "discipline", "zone", "level", "material"].flatMap(
            (key) => hoveredObject.facets?.[key] || [],
          ),
        ),
      ].slice(0, 3)
    : [];
  const hoverRight = size.width - (cardGroups.length ? Math.min(size.width * 0.82, 286) + 8 : 0);
  const hoverWidth = Math.min(270, hoverRight - 16);
  const hoverPlacement =
    hover &&
    (hover.x + hoverWidth + 14 <= hoverRight - 8
      ? { left: hover.x + 14 }
      : hover.x - hoverWidth - 14 >= 8
        ? { right: size.width - hover.x + 14 }
        : { left: 8 });

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
      instance = new SceneRenderer(canvas, (state) => changeRef.current(state), clearHover);
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
  }, [active, canvas, clearHover]);

  useLayoutEffect(() => {
    if (active) renderer.current?.resize(size.width, size.height);
  }, [active, canvas, size.width, size.height]);
  useLayoutEffect(() => {
    if (objects?.length) renderer.current?.load(objects, viewportRef.current);
  }, [active, canvas, objects]);
  useLayoutEffect(() => {
    renderer.current?.setState({ visible, ghost, selected: selection, hovered: hoverId ?? "" });
  }, [active, canvas, visible, ghost, selection, hoverId]);
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
    if (!active || !focused) renderer.current?.stop();
    clearHover();
  }, [active, focused, visible, ghost, measuring, clearHover]);
  useEffect(() => {
    if (!hoverId) return;
    const timer = setTimeout(() => setPreviewedId(hoverId), hoverDelay);
    return () => clearTimeout(timer);
  }, [hoverId]);
  useEffect(
    () => () => {
      if (hoverFrame.current !== undefined) cancelAnimationFrame(hoverFrame.current);
    },
    [],
  );
  useEffect(() => {
    const stop = () => {
      renderer.current?.stop();
      clearHover();
    };
    const visibility = () => {
      if (document.hidden) stop();
    };
    window.addEventListener("blur", stop);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      window.removeEventListener("blur", stop);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [clearHover]);

  const selectObject = (id: string, additive = false) => {
    if (additive && !id) return;
    if (additive && selection.has(id)) {
      changeInspection({
        ...inspection,
        selected: selected.filter((other) => other !== id),
        following: following.filter((other) => other !== id),
      });
    } else {
      changeInspection({
        ...inspection,
        selected: additive ? [...selected, id] : id ? [id] : [],
        following: [...(additive ? following : []), ...(id && !pinned.includes(id) ? [id] : [])],
      });
    }
    clearHover();
  };
  const pickObject = (id: string, additive: boolean) => {
    selectObject(id, additive);
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
  const revealRow = useCallback(
    (path: string) => {
      if (!treeElement) return;
      if (!windowed) {
        treeElement
          .querySelector('[data-tree-path="' + CSS.escape(path) + '"]')
          ?.scrollIntoView({ block: "nearest", inline: "nearest" });
        return;
      }
      const index = rowIndexes.get(path);
      if (index === undefined) return;
      const top = index * treeRowHeight;
      const bottom = top + treeRowHeight;
      if (top < treeElement.scrollTop) treeElement.scrollTop = top;
      else if (bottom > treeElement.scrollTop + treeElement.clientHeight)
        treeElement.scrollTop = bottom - treeElement.clientHeight;
      setTreeScroll(treeElement.scrollTop);
    },
    [treeElement, windowed, rowIndexes],
  );
  useLayoutEffect(() => {
    const lastSelected = selected.at(-1);
    if (lastSelected && document.activeElement === canvas) revealRow(lastSelected);
  }, [selected, canvas, revealRow]);
  useLayoutEffect(() => {
    const path = pendingTreeFocus.current;
    if (path === undefined || !treeElement) return;
    const element = treeElement.querySelector<HTMLElement>(
      '[data-tree-path="' + CSS.escape(path) + '"]',
    );
    if (!element) return;
    pendingTreeFocus.current = undefined;
    element.focus({ preventScroll: true });
  }, [treeElement, treeFocus, rows, start, end]);
  const changeBranch = (row: TreeRow) =>
    setExpanded((previous) => new Map(previous).set(row.path, !row.open));
  const focusRow = (path: string | undefined) => {
    if (!path || !treeElement) return;
    revealRow(path);
    const element = treeElement.querySelector<HTMLElement>(
      '[data-tree-path="' + CSS.escape(path) + '"]',
    );
    if (element) element.focus({ preventScroll: true });
    else {
      pendingTreeFocus.current = path;
      setTreeFocus(path);
    }
  };
  const closeProperties = (id: string) => {
    changeInspection({
      ...inspection,
      following: following.filter((other) => other !== id),
      pinned: pinned.filter((other) => other !== id),
    });
    focusViewport();
  };
  const togglePinned = (id: string) => {
    const locked = pinned.includes(id);
    changeInspection({
      ...inspection,
      following:
        locked && selection.has(id)
          ? [...following, id]
          : following.filter((other) => other !== id),
      pinned: locked ? pinned.filter((other) => other !== id) : [...pinned, id],
    });
    focusViewport();
  };
  const restore = () => {
    setHidden(new Set());
    setGhost(new Set());
    setIsolated(undefined);
  };
  const hideFilter = () => {
    setHidden((old) => new Set([...old, ...matched.map((object) => object.id)]));
    setFilters({});
    clearHover();
  };
  const distance =
    points.length === 2
      ? Math.hypot(...points[0].map((value, i) => points[1][i] - value))
      : undefined;
  const coordinates = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
    };
  };
  const hoverAt = (point: ViewportPointer) => {
    pointer.current = point;
    if (hoverFrame.current !== undefined) return;
    hoverFrame.current = requestAnimationFrame(() => {
      hoverFrame.current = undefined;
      const current = pointer.current;
      if (!current) return;
      const hit = renderer.current?.pick(current.x, current.y);
      if (hit?.id !== hoverTarget.current) {
        hoverTarget.current = hit?.id;
        setPreviewedId(undefined);
        setHover(hit ? { ...current, id: hit.id } : undefined);
      }
    });
  };
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
      <EditorControlSlot>
        <NavigatorMenu
          label={t("剖切")}
          editorActionIcon="scene-section"
          placement="end"
          triggerDisabled={!hasObjects}
          items={([undefined, "x", "y", "z"] as const).map((next) => ({
            label: next ? next.toUpperCase() : t("关闭剖切"),
            checked: axis === next,
            run: () => setAxis(next),
          }))}
        />
        {axis && (
          <input
            aria-label={t("剖切位置")}
            title={t("剖切位置")}
            className="scene-section-range"
            type="range"
            min={0}
            max={1}
            step={0.005}
            value={section}
            onChange={(event) => setSection(Number(event.target.value))}
          />
        )}
        <EditorActionButton
          label={t("测距")}
          icon="ruler"
          disabled={!hasObjects}
          aria-pressed={measuring}
          onClick={() => {
            setPoints([]);
            setMeasuring(!measuring);
            focusViewport();
          }}
        />
        {toggleMaximized && (
          <EditorActionButton
            label={t(maximized ? "退出场景全屏" : "场景全屏")}
            icon={maximized ? "panel-restore" : "panel-maximize"}
            aria-keyshortcuts="Shift+F"
            onClick={toggleMaximized}
          />
        )}
      </EditorControlSlot>
      <span className="sr-only" id={navigationHint}>
        {t("WASD 移动 · Q/E 升降 · 方向键转向 · 左键旋转 / 右键平移")}
      </span>
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
              data-keyboard-focus={keyboardFocus || undefined}
              onFocus={(event) => {
                // Capture the focus entry so navigation keys do not change the indicator.
                setKeyboardFocus(event.currentTarget.matches(":focus-visible"));
              }}
              onContextMenu={(event) => event.preventDefault()}
              onPointerDown={(event) => {
                event.currentTarget.focus({ preventScroll: true });
                setKeyboardFocus(false);
                drag.current = {
                  x: event.clientX,
                  y: event.clientY,
                  button: event.button,
                  distance: 0,
                };
                clearHover();
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
                if (!measuring && !event.buttons && event.pointerType !== "touch")
                  hoverAt(coordinates(event));
              }}
              onPointerUp={(event) => {
                const previous = drag.current;
                drag.current = undefined;
                if (event.currentTarget.hasPointerCapture(event.pointerId))
                  event.currentTarget.releasePointerCapture(event.pointerId);
                // A Control-click may arrive as a secondary button on macOS.
                const primary = previous?.button === 0 || (event.ctrlKey && previous?.button === 2);
                if (!previous || !primary || previous.distance > 4) return;
                const { x, y } = coordinates(event);
                const hit = renderer.current?.pick(x, y);
                if (measuring) {
                  if (hit)
                    setPoints((old) => (old.length === 1 ? [...old, hit.point] : [hit.point]));
                } else pickObject(hit?.id ?? "", event.ctrlKey || event.metaKey);
              }}
              onPointerCancel={() => {
                drag.current = undefined;
                clearHover();
              }}
              onPointerLeave={clearHover}
              onBlur={() => {
                renderer.current?.stop();
                clearHover();
              }}
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
                } else if (event.code === "KeyF" && selected.some((id) => visible.has(id))) {
                  renderer.current?.fit(selected.filter((id) => visible.has(id)));
                } else if (event.key === "Home") {
                  renderer.current?.fit();
                } else if (event.key === "Enter" && selected.length) {
                  changeInspection({
                    ...inspection,
                    following: selected.filter((id) => !pinned.includes(id)),
                  });
                } else return;
                event.preventDefault();
              }}
            />
            {hover && hoveredObject && hoverWidth >= 40 && size.height >= 48 && (
              <div
                className="scene-hover"
                role="tooltip"
                style={{
                  maxWidth: hoverWidth,
                  ...hoverPlacement,
                  top: hover.y + 59 <= size.height ? hover.y + 14 : Math.max(8, hover.y - 57),
                }}
              >
                <span>{hoveredObject.name || hoveredObject.prim_path}</span>
                {hoverFields.length > 0 && <small>{hoverFields.join(" · ")}</small>}
              </div>
            )}
            {!renderError && (
              <SceneCameraControls
                projectPath={projectPath}
                path={path}
                renderer={renderer}
                canFit={visible.size > 0}
                focusViewport={focusViewport}
              />
            )}
            {!visible.size && <div className="scene-view-message">{t("当前没有可见构件")}</div>}
            {measuring && (
              <div className="scene-view-message">
                {distance === undefined
                  ? t(points.length ? "选择第二个点" : "选择两个构件表面上的点")
                  : `${distance.toFixed(3)} m`}
              </div>
            )}
            {cardGroups.length > 0 && (
              <div className="scene-inspection-overlay" aria-label={t("构件属性")}>
                {cardGroups.map(({ kind, label, cards }) => (
                  <div
                    key={kind}
                    className="scene-inspection-group"
                    role="group"
                    aria-label={t(label)}
                    data-inspection-group={kind}
                  >
                    {cards.map((inspected) => {
                      const id = inspected.id;
                      const authored = propertyScene ? properties?.get(id) : inspected.properties;
                      const authoredRows = Object.entries(authored ?? {}).filter(
                        ([key]) =>
                          !key.startsWith("texsys:") || !implicitProperties.has(key.slice(7)),
                      );
                      const locked = pinned.includes(id);
                      const source = sourceReference(scene, id, inspected.semantic_ref ?? "");
                      const relatedIds = inspected.owner_path
                        ? (objects ?? [])
                            .filter((object) => object.owner_path === inspected.owner_path)
                            .map((object) => object.id)
                        : [id];
                      const geometryBounds = inspected.points.reduce(
                        (result, point) => {
                          point.forEach((value, i) => {
                            result.min[i] = Math.min(result.min[i], value);
                            result.max[i] = Math.max(result.max[i], value);
                          });
                          return result;
                        },
                        {
                          min: [Infinity, Infinity, Infinity],
                          max: [-Infinity, -Infinity, -Infinity],
                        },
                      );
                      return (
                        <aside
                          key={id}
                          className="scene-property-panel"
                          aria-label={inspected.name + " " + t("属性")}
                          data-inspected={id}
                        >
                          <div className="scene-panel-heading">
                            <span className="scene-selection-title" title={inspected.prim_path}>
                              {inspected.name}
                            </span>
                            <EditorActionButton
                              icon="pin"
                              label={t(locked ? "取消固定属性" : "固定构件属性")}
                              aria-pressed={locked}
                              onClick={() => togglePinned(id)}
                            />
                            <EditorActionButton
                              icon="close"
                              label={t("关闭构件属性")}
                              onClick={() => closeProperties(id)}
                            />
                          </div>
                          <div className="scene-preview-inspector">
                            <small>
                              {inspected.envelope
                                ? t("包络几何")
                                : (inspected.type ?? t("网格几何"))}
                            </small>
                            <div className="scene-selection-actions">
                              <EditorActionButton
                                icon="locate"
                                label={t("聚焦")}
                                disabled={!visible.has(id)}
                                onClick={() => renderer.current?.fit([id])}
                              />
                              <EditorActionButton
                                icon={hidden.has(id) ? "eye" : "eye-off"}
                                label={t(hidden.has(id) ? "显示" : "隐藏")}
                                aria-pressed={hidden.has(id)}
                                onClick={() => setHidden((old) => toggleSet(old, id))}
                              />
                              <EditorActionButton
                                icon="scene-ghost"
                                label={t("半透明")}
                                aria-pressed={ghost.has(id)}
                                onClick={() => setGhost((old) => toggleSet(old, id))}
                              />
                              <EditorActionButton
                                icon="isolate"
                                label={t("仅显示部件")}
                                onClick={() => {
                                  setHidden(
                                    (old) => new Set([...old].filter((other) => other !== id)),
                                  );
                                  setIsolated(new Set([id]));
                                }}
                              />
                              {relatedIds.length > 1 && (
                                <EditorActionButton
                                  icon="layers"
                                  label={t("仅显示构件")}
                                  onClick={() => {
                                    setHidden(
                                      (old) =>
                                        new Set(
                                          [...old].filter((other) => !relatedIds.includes(other)),
                                        ),
                                    );
                                    setIsolated(new Set(relatedIds));
                                  }}
                                />
                              )}
                              {(hidden.size > 0 || isolated || ghost.size > 0) && (
                                <EditorActionButton
                                  icon="refresh"
                                  label={t("恢复显隐")}
                                  onClick={restore}
                                />
                              )}
                              {source && openSource && (
                                <EditorActionButton
                                  icon="code"
                                  label={t("定位源码")}
                                  onClick={() => openSource(source)}
                                />
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
                            {propertyScene && authored === undefined && (
                              <small>{t("正在读取 USD 属性")}</small>
                            )}
                            {authored === null && (
                              <div className="scene-selection-actions">
                                <small>{t("无法读取 USD 属性")}</small>
                                <EditorActionButton
                                  icon="refresh"
                                  label={t("重试")}
                                  onClick={() => retryProperties(id)}
                                />
                              </div>
                            )}
                            {authoredRows.length > 0 && (
                              <details className="scene-authored" open>
                                <summary>{t("USD 属性")}</summary>
                                <dl className="scene-properties">
                                  {authoredRows.map(([key, row]) => (
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
                        </aside>
                      );
                    })}
                  </div>
                ))}
              </div>
            )}
          </div>
          <aside className="scene-side" aria-label={t("构件检查")}>
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
                      <EditorActionButton
                        icon="clear-search"
                        label={t("清除筛选")}
                        onClick={() => setFilters({})}
                      />
                    )}
                    {filtering && (
                      <EditorActionButton
                        icon="eye-off"
                        label={t("隐藏筛选结果")}
                        disabled={!matched.some((object) => !hidden.has(object.id))}
                        onClick={hideFilter}
                      />
                    )}
                    {(hidden.size > 0 || isolated || ghost.size > 0) && (
                      <EditorActionButton icon="refresh" label={t("恢复显隐")} onClick={restore} />
                    )}
                  </div>
                )}
              </div>
              <div
                ref={setTreeElement}
                className="scene-preview-objects"
                role="tree"
                aria-multiselectable="true"
                aria-label={t("场景对象")}
                onScroll={(event) => {
                  if (windowed) setTreeScroll(event.currentTarget.scrollTop);
                }}
                onKeyDown={(event) => {
                  const additive = event.ctrlKey || event.metaKey;
                  if (
                    event.altKey ||
                    event.nativeEvent.isComposing ||
                    (additive && event.key !== "Enter" && event.key !== " ")
                  )
                    return;
                  const path = (event.target as HTMLElement).dataset.treePath;
                  const index = path ? (rowIndexes.get(path) ?? -1) : -1;
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
                      if (row.object) selectObject(row.object.id, additive);
                      else if (row.children.size) changeBranch(row);
                      break;
                    default:
                      return;
                  }
                  event.preventDefault();
                }}
              >
                <div
                  role="presentation"
                  style={
                    windowed
                      ? { height: rows.length * treeRowHeight, position: "relative" }
                      : undefined
                  }
                >
                  {shownRows.map((row) => {
                    const { path, object, depth, children, open } = row;
                    return (
                      <div
                        key={path}
                        role="treeitem"
                        aria-level={depth + 1}
                        aria-expanded={children.size ? open : undefined}
                        aria-selected={object ? selection.has(object.id) : undefined}
                        className="scene-tree-row"
                        data-tree-path={path}
                        data-prim-path={object?.id}
                        data-hidden={object && !visible.has(object.id) ? "true" : undefined}
                        tabIndex={treeTabStop === path ? 0 : -1}
                        style={{
                          paddingInlineStart: depth * 11 + 4,
                          ...(windowed && {
                            position: "absolute",
                            insetInline: 0,
                            top: rowIndexes.get(path)! * treeRowHeight,
                            height: treeRowHeight,
                          }),
                        }}
                        title={object?.semantic_ref ?? path}
                        onFocus={() => setTreeFocus(path)}
                        onContextMenu={(event) => {
                          if (object && event.ctrlKey) event.preventDefault();
                        }}
                        onPointerUp={(event) => {
                          if (
                            !object ||
                            !event.ctrlKey ||
                            (event.button !== 0 && event.button !== 2) ||
                            (event.target as Element).closest(".scene-tree-expand")
                          )
                            return;
                          event.currentTarget.focus();
                          selectObject(object.id, true);
                        }}
                        onClick={(event) => {
                          if (object && event.ctrlKey) return;
                          event.currentTarget.focus();
                          if (object) selectObject(object.id, event.metaKey);
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
            </div>
          </aside>
        </div>
      ) : (
        <Empty
          title={t(error ? "无法预览此文件" : current ? "此场景没有可预览的几何" : "正在读取场景")}
        >
          {current && t("预览支持 USD 网格、立方体和 BRep 包络。")}
        </Empty>
      )}
    </section>
  );
}
