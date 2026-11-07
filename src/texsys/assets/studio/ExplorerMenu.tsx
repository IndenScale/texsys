import { t } from "./i18n";
import React, { useEffect, useRef } from "react";
import { Icon } from "./ui";

export type ExplorerAction = "create" | "mkdir" | "refresh" | "collapse" | "rename" | "delete";
export function ExplorerMenu({
  x,
  y,
  hasSelection,
  busy,
  close,
  invoke,
}: {
  x: number;
  y: number;
  hasSelection: boolean;
  busy: boolean;
  close: () => void;
  invoke: (action: ExplorerAction) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const origin = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
    return () => origin?.focus();
  }, []);
  return (
    <>
      <button className="core-menu-backdrop" aria-label={t("关闭资源管理器菜单")} onClick={close} />
      <div
        ref={ref}
        className="core-explorer-menu"
        role="menu"
        aria-label={t("资源管理器操作")}
        style={{
          left: Math.max(4, Math.min(x, window.innerWidth - 196)),
          top: Math.max(4, Math.min(y, window.innerHeight - 220)),
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            close();
            return;
          }
          if (event.key === "Tab") {
            close();
            return;
          }
          if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const nodes = Array.from(
            event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"),
          );
          const index = nodes.indexOf(document.activeElement as HTMLButtonElement);
          const next =
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? nodes.length - 1
                : (index + (event.key === "ArrowDown" ? 1 : nodes.length - 1)) % nodes.length;
          nodes[next]?.focus();
        }}
      >
        {(
          [
            ["create", t("新建文件…"), "new-file"],
            ["mkdir", t("新建文件夹…"), "new-folder"],
            ["refresh", t("刷新"), "refresh"],
            ["collapse", t("折叠所有文件夹"), "collapse"],
            ["rename", t("重命名…"), "edit"],
            ["delete", t("删除…"), "trash"],
          ] as const
        ).map(([action, label, icon]) => (
          <button
            key={action}
            role="menuitem"
            className={action === "rename" ? "core-menu-group-start" : undefined}
            disabled={busy || (["rename", "delete"].includes(action) && !hasSelection)}
            onClick={() => invoke(action)}
          >
            <Icon name={icon} size={14} />
            <span>{label}</span>
            {action === "rename" && <kbd>F2</kbd>}
          </button>
        ))}
      </div>
    </>
  );
}
