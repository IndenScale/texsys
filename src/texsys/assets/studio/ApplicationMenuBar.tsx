import { t } from "./i18n";
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { CommandItem, MenuGroup } from "./menus";
import { visibleMenuCount } from "./menuLayout";
import { AnimatedPresence } from "./AnimatedPresence";
import { Keycaps } from "./Keycaps";
import { Icon } from "./ui";

const MORE = "__overflow";
const enabledRows = (panel: HTMLElement | null) =>
  Array.from(
    panel?.querySelectorAll<HTMLButtonElement>(
      'button[role^="menuitem"]:not([aria-disabled="true"])',
    ) ?? [],
  );

export function ApplicationMenuBar({
  groups,
  commands,
  invoke,
  onOpen,
  dismiss,
}: {
  groups: MenuGroup[];
  commands: CommandItem[];
  invoke: (id: string) => void;
  onOpen: () => void;
  dismiss: boolean;
}) {
  const root = useRef<HTMLDivElement>(null),
    measure = useRef<HTMLDivElement>(null);
  const popup = useRef<HTMLDivElement>(null),
    submenu = useRef<HTMLDivElement>(null);
  const triggers = useRef(new Map<string, HTMLButtonElement>());
  const [count, setCount] = useState(groups.length);
  const [openId, setOpenId] = useState<string | null>(null);
  const [subId, setSubId] = useState<string | null>(null);
  const [tabStop, setTabStop] = useState(groups[0]?.id ?? MORE);
  const [position, setPosition] = useState({ left: 8, top: 40 });
  const [subPosition, setSubPosition] = useState({ left: 8, top: 40 });
  const mainFocus = useRef<"first" | "last" | null>(null),
    subFocus = useRef(false);
  const visible = groups.slice(0, count),
    overflow = groups.slice(count);
  const topIds = [...visible.map((group) => group.id), ...(overflow.length ? [MORE] : [])];
  const activeGroup = groups.find((group) => group.id === (openId === MORE ? subId : openId));
  const lookup = (id: string) => commands.find((command) => command.id === id);
  const close = (restore = false) => {
    if (restore)
      (
        triggers.current.get(openId ?? tabStop) ??
        root.current?.querySelector<HTMLButtonElement>('button[role="menuitem"]')
      )?.focus();
    setOpenId(null);
    setSubId(null);
  };
  const open = (id: string, focus: "first" | "last" | null = null) => {
    mainFocus.current = focus;
    if (id === openId && focus) {
      const rows = enabledRows(popup.current);
      rows[focus === "last" ? rows.length - 1 : 0]?.focus();
      mainFocus.current = null;
    }
    setTabStop(id);
    setOpenId(id);
    setSubId(null);
    onOpen();
  };
  const showSubmenu = (id: string, focus = false) => {
    subFocus.current = focus;
    setSubId(id);
    if (focus && id === subId) enabledRows(submenu.current)[0]?.focus();
  };
  const choose = (command: CommandItem) => {
    if (command.disabled) return;
    close(true);
    invoke(command.id);
  };
  useLayoutEffect(() => {
    if (!root.current || !measure.current) return;
    const container = root.current.parentElement;
    if (!container) return;
    const recalculate = () => {
      const labels = Array.from(measure.current!.children);
      const widths = labels.slice(0, groups.length).map((el) => el.getBoundingClientRect().width);
      const style = getComputedStyle(container);
      const siblings = Array.from(container.children).filter((el) => el !== root.current);
      const available =
        container.clientWidth -
        (Number.parseFloat(style.paddingLeft) || 0) -
        (Number.parseFloat(style.paddingRight) || 0) -
        siblings.reduce((sum, el) => sum + el.getBoundingClientRect().width, 0) -
        (Number.parseFloat(style.columnGap) || 0) * siblings.length;
      setCount(
        visibleMenuCount(
          available,
          widths,
          labels[groups.length]?.getBoundingClientRect().width ?? 40,
        ),
      );
    };
    recalculate();
    const observer = new ResizeObserver(recalculate);
    observer.observe(container);
    observer.observe(measure.current);
    return () => observer.disconnect();
  }, [groups]);
  const previousCount = useRef(count);
  useLayoutEffect(() => {
    if (!topIds.includes(tabStop)) setTabStop(overflow.length ? MORE : (topIds[0] ?? MORE));
    if (previousCount.current !== count && openId)
      close(
        !!popup.current?.contains(document.activeElement) ||
          !!submenu.current?.contains(document.activeElement),
      );
    previousCount.current = count;
  }, [count]);
  useLayoutEffect(() => {
    if (!openId || !popup.current) return;
    const anchor = triggers.current.get(openId)?.getBoundingClientRect();
    if (anchor)
      setPosition({
        left: Math.max(8, Math.min(anchor.left, window.innerWidth - popup.current.offsetWidth - 8)),
        top: anchor.bottom + 6,
      });
    const rows = enabledRows(popup.current);
    if (mainFocus.current) rows[mainFocus.current === "last" ? rows.length - 1 : 0]?.focus();
    mainFocus.current = null;
  }, [openId]);
  useLayoutEffect(() => {
    if (!subId || !submenu.current) return;
    const row = popup.current
      ?.querySelector<HTMLElement>(`[data-menu-group="${subId}"]`)
      ?.getBoundingClientRect();
    if (row) {
      const width = submenu.current.offsetWidth,
        height = submenu.current.offsetHeight;
      setSubPosition({
        left:
          row.right + width + 12 <= window.innerWidth
            ? row.right + 4
            : Math.max(8, row.left - width - 4),
        top: Math.max(8, Math.min(row.top - 4, window.innerHeight - height - 8)),
      });
    }
    if (subFocus.current) enabledRows(submenu.current)[0]?.focus();
    subFocus.current = false;
  }, [subId]);
  useEffect(() => {
    if (dismiss) close();
  }, [dismiss]);
  useEffect(() => {
    if (!openId) return;
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      const inTrigger =
        root.current?.contains(target) &&
        target instanceof Element &&
        !!target.closest('button[role="menuitem"]');
      if (!inTrigger && !popup.current?.contains(target) && !submenu.current?.contains(target))
        close();
    };
    const blur = () => close();
    const resize = () =>
      close(
        !!popup.current?.contains(document.activeElement) ||
          !!submenu.current?.contains(document.activeElement),
      );
    document.addEventListener("pointerdown", outside, true);
    window.addEventListener("blur", blur);
    window.addEventListener("resize", resize);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("blur", blur);
      window.removeEventListener("resize", resize);
    };
  }, [openId]);
  useEffect(() => {
    const activate = (event: KeyboardEvent) => {
      if (
        event.key === "F10" &&
        !event.shiftKey &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey
      ) {
        event.preventDefault();
        if (openId) close(true);
        else (triggers.current.get(tabStop) ?? triggers.current.values().next().value)?.focus();
      }
    };
    window.addEventListener("keydown", activate);
    return () => window.removeEventListener("keydown", activate);
  }, [openId, tabStop]);
  const switchTop = (step: number) => {
    const index = Math.max(0, topIds.indexOf(openId ?? tabStop));
    const id = topIds[(index + step + topIds.length) % topIds.length];
    triggers.current.get(id)?.focus();
    open(id, "first");
  };
  const menuKeys = (event: React.KeyboardEvent<HTMLDivElement>, isSubmenu = false) => {
    const panel = isSubmenu ? submenu.current : popup.current;
    const rows = enabledRows(panel),
      index = rows.indexOf(document.activeElement as HTMLButtonElement);
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      event.stopPropagation();
      const next =
        event.key === "Home"
          ? 0
          : event.key === "End"
            ? rows.length - 1
            : (index + (event.key === "ArrowDown" ? 1 : rows.length - 1)) % rows.length;
      rows[next]?.focus();
    } else if (event.key === "Escape" || (isSubmenu && event.key === "ArrowLeft")) {
      event.preventDefault();
      event.stopPropagation();
      if (isSubmenu) {
        popup.current?.querySelector<HTMLButtonElement>(`[data-menu-group="${subId}"]`)?.focus();
        setSubId(null);
      } else close(true);
    } else if (event.key === "Tab") close(true);
    else if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      event.stopPropagation();
      const groupId = (event.target as HTMLElement).getAttribute("data-menu-group");
      if (!isSubmenu && event.key === "ArrowRight" && groupId) showSubmenu(groupId, true);
      else switchTop(event.key === "ArrowRight" ? 1 : -1);
    }
  };
  const renderCommands = (group: MenuGroup | undefined) =>
    group?.items.map((item) => {
      const command = lookup(item.id);
      if (!command) return null;
      return (
        <button
          key={item.id}
          role={command.checked === undefined ? "menuitem" : "menuitemcheckbox"}
          aria-checked={command.checked}
          aria-disabled={!!command.disabled}
          disabled={!!command.disabled}
          tabIndex={-1}
          onClick={() => choose(command)}
        >
          <span className="application-menu-mark">
            {command.checked && <Icon name="tick" size={14} />}
          </span>
          <span className="application-menu-label">{command.label}</span>
          {command.keys && <Keycaps keys={command.keys} />}
        </button>
      );
    });
  return (
    <div
      ref={root}
      className="application-menu-bar"
      role="menubar"
      aria-label={t("应用菜单")}
      onBlur={(event) => {
        const target = event.relatedTarget as Node | null;
        if (
          target &&
          !root.current?.contains(target) &&
          !popup.current?.contains(target) &&
          !submenu.current?.contains(target)
        )
          close();
      }}
    >
      <div ref={measure} className="application-menu-measure" aria-hidden="true" inert>
        {groups.map((group) => (
          <span className="application-menu-trigger" key={group.id}>
            {group.label}
          </span>
        ))}
        <span className="application-menu-trigger">{t("更多")}</span>
      </div>
      {topIds.map((id) => (
        <button
          key={id}
          ref={(el) => {
            if (el) triggers.current.set(id, el);
            else triggers.current.delete(id);
          }}
          className="application-menu-trigger"
          role="menuitem"
          aria-label={id === MORE ? t("更多菜单") : undefined}
          aria-haspopup="menu"
          aria-expanded={openId === id}
          tabIndex={id === tabStop ? 0 : -1}
          onFocus={() => setTabStop(id)}
          onClick={() => (openId === id ? close(true) : open(id))}
          onKeyDown={(event) => {
            if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
              event.preventDefault();
              open(id, event.key === "ArrowUp" ? "last" : "first");
            } else if (["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) {
              event.preventDefault();
              const index = topIds.indexOf(id),
                next =
                  event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? topIds.length - 1
                      : (index + (event.key === "ArrowRight" ? 1 : topIds.length - 1)) %
                        topIds.length;
              const nextId = topIds[next];
              triggers.current.get(nextId)?.focus();
              if (openId) open(nextId, "first");
            } else if (event.key === "Escape") {
              event.preventDefault();
              close(true);
            }
          }}
        >
          {id === MORE ? t("更多") : groups.find((group) => group.id === id)?.label}
        </button>
      ))}
      {createPortal(
        <>
          <AnimatedPresence show={!!openId}>
            <div
              ref={popup}
              className={`application-menu-popup${openId === MORE ? " is-overflow" : ""}`}
              role="menu"
              aria-label={
                openId === MORE
                  ? t("更多应用菜单")
                  : groups.find((group) => group.id === openId)?.label
              }
              style={position}
              onKeyDown={(event) => menuKeys(event)}
            >
              {openId === MORE
                ? overflow.map((group) => (
                    <button
                      key={group.id}
                      role="menuitem"
                      tabIndex={-1}
                      data-menu-group={group.id}
                      aria-haspopup="menu"
                      aria-expanded={subId === group.id}
                      onPointerEnter={() => showSubmenu(group.id)}
                      onClick={() => showSubmenu(group.id, true)}
                    >
                      <span className="application-menu-label">{group.label}</span>
                      <span aria-hidden="true">›</span>
                    </button>
                  ))
                : renderCommands(activeGroup)}
            </div>
          </AnimatedPresence>
          <AnimatedPresence show={openId === MORE && !!subId}>
            <div
              ref={submenu}
              className="application-menu-popup"
              role="menu"
              aria-label={activeGroup?.label}
              style={subPosition}
              onKeyDown={(event) => menuKeys(event, true)}
            >
              {renderCommands(activeGroup)}
            </div>
          </AnimatedPresence>
        </>,
        document.body,
      )}
    </div>
  );
}
