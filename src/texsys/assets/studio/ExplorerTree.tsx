import { t } from "./i18n";
import React, { type CSSProperties } from "react";
import { FileIcon } from "./FileIcon";
import { Icon } from "./ui";
import type { TreeEntry } from "./workspace-model";

/** Flat visible rows share fixed twistie/icon slots; depth only changes the indent. */
export function ExplorerTree({
  entries,
  expanded,
  selected,
  dirtyPaths,
  select,
  toggle,
  open,
  contextMenu,
  rename,
  remove,
}: {
  entries: TreeEntry[];
  expanded: Set<string>;
  selected: string;
  dirtyPaths: Set<string>;
  select: (path: string) => void;
  toggle: (path: string) => void;
  open: (path: string) => void;
  contextMenu: (path: string, x: number, y: number) => void;
  rename: () => void;
  remove: () => void;
}) {
  const selectedEntry = entries.find((entry) => entry.path === selected);
  const activeBranch = selectedEntry
    ? selectedEntry.directory && expanded.has(selected)
      ? selected
      : selected.split("/").slice(0, -1).join("/")
    : undefined;
  const tabStop = selectedEntry?.path ?? entries[0]?.path;
  return (
    <div
      className="core-file-tree"
      role="tree"
      aria-label={t("项目文件")}
      onKeyDown={(event) => {
        if (event.key === "F2" && selectedEntry) {
          event.preventDefault();
          rename();
          return;
        }
        if (event.key === "Delete" && selectedEntry) {
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
            buttons[entries.findIndex((item) => item.path === parent)]?.focus();
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
      {entries.map((entry) => {
        const parts = entry.path.split("/");
        // The project root is an ancestor too. Each guide sits under its ancestor's twistie.
        const ancestors = [
          "",
          ...parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/")),
        ];
        return (
          <button
            key={entry.path}
            tabIndex={entry.path === tabStop ? 0 : -1}
            role="treeitem"
            aria-level={entry.depth + 1}
            aria-selected={selected === entry.path}
            aria-expanded={entry.directory ? expanded.has(entry.path) : undefined}
            className={`core-tree-row ${selected === entry.path ? "selected" : ""}`}
            style={{ "--tree-depth": entry.depth + 1 } as CSSProperties}
            title={entry.path}
            onFocus={() => select(entry.path)}
            onContextMenu={(event) => {
              event.preventDefault();
              select(entry.path);
              contextMenu(entry.path, event.clientX, event.clientY);
            }}
            onClick={() => {
              select(entry.path);
              if (entry.directory) toggle(entry.path);
              else open(entry.path);
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
              {entry.directory && (
                <Icon name="chevron" size={12} expanded={expanded.has(entry.path)} />
              )}
            </span>
            <span className="core-tree-icon" aria-hidden="true">
              {entry.directory ? <Icon name="folder" size={16} /> : <FileIcon path={entry.path} />}
            </span>
            <span className="core-entry-name">{entry.name}</span>
            {dirtyPaths.has(entry.path) && <span className="core-dirty" title={t("未保存")} />}
          </button>
        );
      })}
    </div>
  );
}
