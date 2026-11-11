import { t } from "./i18n";
import React, { useLayoutEffect, useRef, type CSSProperties } from "react";
import { FileIcon } from "./FileIcon";
import { Icon } from "./ui";
import { explorerCreationIndex, type TreeEntry } from "./workspace-model";

export type ExplorerCreation = {
  action: "create" | "mkdir";
  parent: string;
  value: string;
  error?: string;
  focus: number;
};

/** Flat visible rows share fixed twistie/icon slots; depth only changes the indent. */
export function ExplorerTree({
  entries,
  expanded,
  selected,
  dirtyPaths,
  label = t("项目文件"),
  select,
  toggle,
  open,
  contextMenu,
  rename,
  remove,
  creation,
  creating,
  changeCreation,
  submitCreation,
  cancelCreation,
  focusRequest,
  focusHandled,
  focusParent,
}: {
  entries: TreeEntry[];
  expanded: Set<string>;
  selected: string;
  dirtyPaths?: Set<string>;
  label?: string;
  select: (path: string) => void;
  toggle: (path: string) => void;
  open: (path: string, keyboard: boolean) => void;
  contextMenu?: (path: string, x: number, y: number) => void;
  rename?: () => void;
  remove?: () => void;
  creation?: ExplorerCreation;
  creating?: boolean;
  changeCreation?: (value: string) => void;
  submitCreation?: () => void;
  cancelCreation?: () => void;
  focusRequest?: { path: string; token: number };
  focusHandled?: () => void;
  focusParent?: () => void;
}) {
  const treeRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const focusedRequest = useRef<number | undefined>(undefined);
  useLayoutEffect(() => {
    if (creation) {
      inputRef.current?.focus();
      inputRef.current?.scrollIntoView({ block: "nearest" });
    }
  }, [creation?.focus]);
  useLayoutEffect(() => {
    if (!focusRequest || focusedRequest.current === focusRequest.token) return;
    const row = Array.from(
      treeRef.current?.querySelectorAll<HTMLButtonElement>("[data-tree-path]") ?? [],
    ).find((item) => item.dataset.treePath === focusRequest.path);
    if (row) {
      row.focus();
      row.scrollIntoView({ block: "nearest" });
      focusedRequest.current = focusRequest.token;
      focusHandled?.();
    }
  }, [entries, focusRequest]);
  const selectedEntry = entries.find((entry) => entry.path === selected);
  const activeBranch = selectedEntry
    ? selectedEntry.directory && expanded.has(selected)
      ? selected
      : selected.split("/").slice(0, -1).join("/")
    : undefined;
  const tabStop = selectedEntry?.path ?? entries[0]?.path;
  const renderEntry = (entry: TreeEntry) => {
    const parts = entry.path.split("/");
    const ancestors = [
      "",
      ...parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/")),
    ];
    const split = entry.directory && entry.opensDocument;
    const isExpanded = expanded.has(entry.path);
    const indent = { "--tree-depth": entry.depth + 1 } as CSSProperties;
    const row = (
      <button
        key={entry.path}
        data-tree-path={entry.path}
        tabIndex={entry.path === tabStop ? 0 : -1}
        role="treeitem"
        aria-level={entry.depth + 1}
        aria-description={entry.detail}
        aria-selected={selected === entry.path}
        aria-expanded={entry.directory ? isExpanded : undefined}
        className={`core-tree-row ${selected === entry.path ? "selected" : ""}`}
        style={indent}
        title={
          entry.detail ? `${entry.name} · ${entry.detail}` : entry.virtual ? entry.name : entry.path
        }
        onFocus={() => select(entry.path)}
        onContextMenu={(event) => {
          if (entry.virtual || !contextMenu) return;
          event.preventDefault();
          select(entry.path);
          contextMenu(entry.path, event.clientX, event.clientY);
        }}
        onClick={(event) => {
          select(entry.path);
          if (entry.directory && !split) toggle(entry.path);
          else open(entry.path, event.detail === 0);
        }}
      >
        <span className="core-tree-guides" aria-hidden="true">
          {ancestors.map((path, depth) => (
            <span
              key={path}
              className={`core-tree-guide ${path === activeBranch ? "active" : ""}`}
              style={{ "--guide-depth": depth } as CSSProperties}
            />
          ))}
        </span>
        <span className="core-tree-twistie" aria-hidden="true">
          {entry.directory && !split && <Icon name="chevron" size={12} expanded={isExpanded} />}
        </span>
        <span className="core-tree-icon" aria-hidden="true">
          {entry.icon || entry.directory ? (
            <Icon name={entry.icon ?? "folder"} size={16} />
          ) : (
            <FileIcon path={entry.path} />
          )}
        </span>
        <span className="core-entry-name">{entry.name}</span>
        {entry.detail && (
          <small className="core-tree-detail" aria-hidden="true">
            {entry.detail}
          </small>
        )}
        {dirtyPaths?.has(entry.path) && <span className="core-dirty" title={t("未保存")} />}
      </button>
    );
    if (!split) return row;
    return (
      <div
        className="core-tree-document-folder"
        role="presentation"
        style={indent}
        key={entry.path}
      >
        {row}
        <button
          type="button"
          className="core-tree-document-toggle"
          tabIndex={-1}
          aria-label={t(isExpanded ? "折叠 {0}" : "展开 {0}", { 0: entry.name })}
          aria-expanded={isExpanded}
          onClick={(event) => {
            (event.currentTarget.previousElementSibling as HTMLButtonElement).focus();
            toggle(entry.path);
          }}
        >
          <Icon name="chevron" size={12} expanded={isExpanded} />
        </button>
      </div>
    );
  };
  const rows = entries.map(renderEntry);
  if (creation) {
    const index = explorerCreationIndex(entries, creation.parent, creation.action === "mkdir");
    if (index >= 0 && (!creation.parent || expanded.has(creation.parent)))
      rows.splice(
        index,
        0,
        <div
          key="creation"
          className="core-tree-creation"
          style={
            {
              "--tree-depth": creation.parent ? creation.parent.split("/").length + 1 : 1,
            } as CSSProperties
          }
        >
          <div className="core-tree-row">
            <span className="core-tree-twistie" aria-hidden="true">
              {creation.action === "mkdir" && <Icon name="chevron" size={12} expanded={false} />}
            </span>
            <span className="core-tree-icon" aria-hidden="true">
              {creation.action === "mkdir" ? (
                <Icon name="folder" size={16} />
              ) : (
                <FileIcon path={creation.value} />
              )}
            </span>
            <input
              ref={inputRef}
              aria-label={t(creation.action === "mkdir" ? "新文件夹名称" : "新文件名称")}
              value={creation.value}
              readOnly={creating}
              aria-busy={creating}
              aria-invalid={Boolean(creation.error)}
              aria-describedby={creation.error ? "core-tree-create-error" : undefined}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => changeCreation?.(event.target.value)}
              onKeyDown={(event) => {
                event.stopPropagation();
                if (event.nativeEvent.isComposing || event.keyCode === 229 || creating) return;
                if (event.key === "Enter") {
                  event.preventDefault();
                  submitCreation?.();
                } else if (event.key === "Escape") {
                  event.preventDefault();
                  cancelCreation?.();
                }
              }}
            />
          </div>
          {creation.error && (
            <div id="core-tree-create-error" className="core-tree-create-error" role="alert">
              {creation.error}
            </div>
          )}
        </div>,
      );
  }
  return (
    <div
      ref={treeRef}
      className="core-file-tree"
      role="tree"
      aria-label={label}
      onKeyDown={(event) => {
        if (event.key === "F2" && selectedEntry && !selectedEntry.virtual && rename) {
          event.preventDefault();
          rename();
          return;
        }
        if (event.key === "Delete" && selectedEntry && !selectedEntry.virtual && remove) {
          event.preventDefault();
          remove();
          return;
        }
        if (!["ArrowDown", "ArrowUp", "Home", "End", "ArrowRight", "ArrowLeft"].includes(event.key))
          return;
        const buttons = Array.from(
          event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="treeitem"]'),
        );
        const index = buttons.indexOf(event.target as HTMLButtonElement);
        const entry = entries[index];
        if (!entry) return;
        event.preventDefault();
        if (focusParent && (event.key === "Home" || (event.key === "ArrowUp" && index === 0))) {
          focusParent();
          return;
        }
        if (event.key === "ArrowRight") {
          if (!entry.directory) return;
          if (!expanded.has(entry.path)) toggle(entry.path);
          else if (entries[index + 1]?.depth === entry.depth + 1) buttons[index + 1]?.focus();
          return;
        }
        if (event.key === "ArrowLeft") {
          if (entry.directory && expanded.has(entry.path)) toggle(entry.path);
          else {
            const parent = entry.path.split("/").slice(0, -1).join("/");
            if (!parent) focusParent?.();
            else buttons[entries.findIndex((item) => item.path === parent)]?.focus();
          }
          return;
        }
        const target =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? buttons.length - 1
              : event.key === "ArrowUp"
                ? Math.max(0, index - 1)
                : Math.min(buttons.length - 1, index + 1);
        buttons[target]?.focus();
      }}
    >
      {rows}
    </div>
  );
}
