import React, { useRef, useState, type RefObject } from "react";
import { EditorActionButton } from "./EditorActionButton";
import { NavigatorMenu, type NavigatorMenuItem } from "./NavigatorMenu";
import { t } from "./i18n";
import type { SceneViewport } from "./preview-viewport";
import type { SceneRenderer, ViewDirection } from "./scene-renderer";
import { Icon } from "./ui";

type FixedView = { name: string; viewport: SceneViewport };

const standardViews: [ViewDirection, string][] = [
  ["iso", "等轴测"],
  ["top", "俯视"],
  ["front", "正视"],
  ["side", "侧视"],
];

function readViews(key: string): FixedView[] {
  try {
    const values = JSON.parse(localStorage.getItem(key) ?? "[]");
    if (!Array.isArray(values)) return [];
    const point = (value: unknown) =>
      Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);
    return values.filter(
      (item) =>
        typeof item?.name === "string" &&
        item.name.trim() &&
        item.viewport?.kind === "scene" &&
        point(item.viewport.eye) &&
        point(item.viewport.at) &&
        ["perspective", "orthographic"].includes(item.viewport.projection) &&
        Number.isFinite(item.viewport.span) &&
        item.viewport.span > 0 &&
        Number.isFinite(item.viewport.zoom) &&
        item.viewport.zoom > 0,
    );
  } catch {
    return [];
  }
}

export function SceneCameraControls({
  projectPath,
  path,
  renderer,
  scale = 1,
  canFit,
  focusViewport,
}: {
  projectPath: string;
  path: string;
  renderer: RefObject<SceneRenderer | null>;
  scale?: number;
  canFit: boolean;
  focusViewport: () => void;
}) {
  const storageKey = `texsys.studio.scene-views:${JSON.stringify([projectPath, path])}`;
  const [views, setViews] = useState(() => readViews(storageKey));
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const pendingView = useRef<SceneViewport | null>(null);
  const saveLabel = t("保存当前机位");
  const update = (values: FixedView[]) => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(values));
      setViews(values);
      return true;
    } catch {
      setError(t("无法在此浏览器保存固定机位。"));
      return false;
    }
  };
  const deleteView = (name: string) => {
    setError("");
    update(readViews(storageKey).filter((view) => view.name !== name));
  };
  const items: NavigatorMenuItem[] = standardViews.map(([direction, label]) => ({
    label: t(label),
    run: canFit
      ? () => {
          renderer.current?.fit(undefined, direction);
          focusViewport();
        }
      : undefined,
  }));
  if (views.length)
    items.push(
      { label: "", separator: true },
      ...views.map(({ name, viewport }) => ({
        id: `fixed:${name}`,
        label: name,
        icon: "cube",
        run: () => {
          renderer.current?.applyView(viewport);
          focusViewport();
        },
      })),
    );
  items.push({
    label: t("保存当前机位…"),
    separator: true,
    run: () => {
      if (!renderer.current || !dialog.current) return;
      pendingView.current = renderer.current.captureView();
      setName("");
      setError("");
      dialog.current.showModal();
    },
  });
  if (views.length)
    items.push({
      label: t("删除固定机位"),
      submenu: views.map(({ name }) => ({ label: name, run: () => deleteView(name) })),
    });

  return (
    <>
      <div className="scene-camera-controls" role="group" aria-label={t("场景导航")}>
        <div className="scene-zoom-control">
          <Icon name="search-plus" size={16} />
          <input
            type="range"
            aria-label={t("缩放")}
            aria-valuetext={`${Math.round(scale * 100)}%`}
            title={t("缩放")}
            min={-3}
            max={3}
            step={0.01}
            value={Math.max(-3, Math.min(3, Math.log2(scale)))}
            onInput={(event) => renderer.current?.setZoom(2 ** event.currentTarget.valueAsNumber)}
          />
        </div>
        <div className="scene-view-controls">
          <EditorActionButton
            label={t("适应视图")}
            icon="fit"
            disabled={!canFit}
            onClick={() => renderer.current?.fit()}
          />
          <NavigatorMenu
            label={t("视角")}
            items={items}
            direction="up"
            triggerClassName="scene-view-trigger"
            onOpen={() => {
              setError("");
              setViews(readViews(storageKey));
            }}
            triggerIcon={
              <>
                <Icon name="cube" size={16} />
                <Icon name="chevron-up" size={14} />
              </>
            }
          />
        </div>
      </div>
      {error && !dialog.current?.open && (
        <div className="scene-camera-error" role="status">
          {error}
        </div>
      )}
      <dialog
        ref={dialog}
        className="scene-camera-dialog"
        aria-label={saveLabel}
        onClose={() => {
          pendingView.current = null;
          setError("");
        }}
        onKeyDown={(event) => event.stopPropagation()}
      >
        <form
          className="core-dialog"
          onSubmit={(event) => {
            event.preventDefault();
            const viewport = pendingView.current;
            const trimmed = name.trim();
            if (!viewport || !trimmed) return;
            const current = readViews(storageKey);
            if (current.some((view) => view.name === trimmed)) {
              setError(t("已有同名机位。"));
              return;
            }
            if (update([...current, { name: trimmed, viewport }])) dialog.current?.close();
          }}
        >
          <strong>{saveLabel}</strong>
          <label>
            {t("机位名称")}
            <input
              autoFocus
              required
              maxLength={80}
              value={name}
              onChange={(event) => {
                setName(event.target.value);
                setError("");
              }}
            />
          </label>
          {error && (
            <p className="core-error" role="alert">
              {error}
            </p>
          )}
          <div className="core-dialog-actions">
            <button type="button" onClick={() => dialog.current?.close()}>
              {t("取消")}
            </button>
            <button className="core-primary" disabled={!name.trim()}>
              {t("保存")}
            </button>
          </div>
        </form>
      </dialog>
    </>
  );
}
