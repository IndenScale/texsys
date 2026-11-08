import React, { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Keycaps } from "./Keycaps";
import { NavigatorButton } from "./NavigatorPanel";
import { Icon } from "./ui";

export type NavigatorMenuItem = {
  label: string;
  run?: () => void;
  icon?: string;
  checked?: boolean;
  checkbox?: boolean;
  reason?: string;
  separator?: boolean;
  shortcut?: string[];
  submenu?: NavigatorMenuItem[];
};

function placeMenu(
  menu: HTMLElement,
  { left, right, top, bottom }: Pick<DOMRect, "left" | "right" | "top" | "bottom">,
  placement: "end" | "start" | "side" | "point",
  direction: "down" | "up" | "auto" = "auto",
) {
  const edge = 6;
  const gap = 3;
  menu.style.maxHeight = "none";
  menu.style.overflow = "visible";
  menu.style.overflowY = "visible";

  const { width, height } = menu.getBoundingClientRect();
  const maxX = window.innerWidth - width - edge;
  const maxY = window.innerHeight - height - edge;
  const preferSide = placement === "side" && right <= maxX;
  const x = preferSide ? right : placement === "point" || placement === "start" ? left : right - width;

  const spaceBelow = window.innerHeight - bottom - gap - edge;
  const spaceAbove = top - gap - edge;

  let y: number;
  if (placement === "point") {
    // Context menu (right-click point): standard VS Code / native OS behavior.
    // Start at cursor y; if space below is limited, clamp bottom to window.innerHeight - edge
    // so the menu spans across the cursor point ("上面有一点，下面有一点") without scrolling.
    y = Math.max(edge, Math.min(top, window.innerHeight - height - edge));
  } else if (direction === "down") {
    y = bottom + gap;
    if (y + height > window.innerHeight - edge && spaceAbove > spaceBelow) {
      y = top - height - gap;
    }
  } else if (direction === "up") {
    y = top - height - gap;
  } else {
    if (spaceBelow >= height) {
      y = bottom + gap;
    } else if (spaceAbove >= height) {
      y = top - height - gap;
    } else if (spaceAbove > spaceBelow) {
      y = top - height - gap;
    } else {
      y = bottom + gap;
    }
  }

  const finalX = Math.max(edge, Math.min(x, maxX));
  const finalY = Math.max(edge, Math.min(y, maxY));
  menu.style.left = `${finalX}px`;
  menu.style.top = `${finalY}px`;
}

export function NavigatorMenu({
  label,
  title = label,
  items,
  point,
  onDismiss,
  placement = "end",
  direction = "auto",
  triggerClassName,
  triggerIcon = "more",
}: {
  label: string;
  title?: string;
  items: NavigatorMenuItem[];
  point?: { x: number; y: number };
  onDismiss?: () => void;
  placement?: "end" | "start" | "side";
  direction?: "down" | "up" | "auto";
  triggerClassName?: string;
  triggerIcon?: ReactNode;
}) {
  const id = useId();
  const menuRef = useRef<HTMLDivElement>(null);
  const submenuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [activeSubmenuIndex, setActiveSubmenuIndex] = useState<number | null>(null);

  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu || !point) return;
    if (!menu.matches(":popover-open")) menu.showPopover();
    placeMenu(menu, { left: point.x, right: point.x, top: point.y, bottom: point.y }, "point", direction);
    (menu.querySelector<HTMLButtonElement>("button:not(:disabled)") ?? menu).focus();
  }, [point, direction]);

  useLayoutEffect(() => {
    const sub = submenuRef.current;
    if (!sub || activeSubmenuIndex === null || !items[activeSubmenuIndex]?.submenu) return;
    const parentMenu = menuRef.current;
    if (!parentMenu) return;
    const parentButtons = Array.from(parentMenu.querySelectorAll<HTMLButtonElement>(":scope > button"));
    const button = parentButtons[activeSubmenuIndex];
    if (!button) return;
    const rect = button.getBoundingClientRect();
    const { width, height } = sub.getBoundingClientRect();
    const edge = 4;
    const spaceRight = window.innerWidth - rect.right - edge;
    const shouldGoRight = spaceRight >= width || spaceRight >= rect.left;
    const left = shouldGoRight
      ? Math.min(rect.right + 2, window.innerWidth - width - edge)
      : Math.max(edge, rect.left - width - 2);
    const top = Math.max(edge, Math.min(rect.top - 4, window.innerHeight - height - edge));
    sub.style.left = `${left}px`;
    sub.style.top = `${top}px`;
    sub.style.maxHeight = "none";
    sub.style.overflow = "visible";
    sub.style.overflowY = "visible";
  }, [activeSubmenuIndex, items]);

  useEffect(() => {
    if (!open) return;
    const dismiss = () => {
      if (menuRef.current?.matches(":popover-open")) menuRef.current.hidePopover();
    };
    window.addEventListener("resize", dismiss);
    return () => window.removeEventListener("resize", dismiss);
  }, [open]);

  return (
    <>
      {!point && (
        <NavigatorButton
          className={triggerClassName}
          label={label}
          title={title}
          icon={triggerIcon}
          aria-haspopup="menu"
          aria-expanded={open}
          popoverTarget={id}
          onClick={(event) => {
            const menu = menuRef.current;
            if (!menu) return;
            event.preventDefault();
            if (menu.matches(":popover-open")) {
              menu.hidePopover();
              return;
            }
            menu.showPopover();
            placeMenu(menu, event.currentTarget.getBoundingClientRect(), placement, direction);
            (menu.querySelector<HTMLButtonElement>("button:not(:disabled)") ?? menu).focus();
          }}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" && !open) {
              event.preventDefault();
              event.currentTarget.click();
            }
          }}
        />
      )}
      <div
        className="navigator-menu"
        id={id}
        ref={menuRef}
        role="menu"
        aria-label={label}
        popover="auto"
        tabIndex={-1}
        onToggle={(event) => {
          const isOpen = event.newState === "open";
          setOpen(isOpen);
          if (!isOpen) {
            setActiveSubmenuIndex(null);
            onDismiss?.();
          }
        }}
        onPointerLeave={(event) => {
          const next = event.relatedTarget as Node | null;
          if (!submenuRef.current?.contains(next)) {
            setActiveSubmenuIndex(null);
          }
        }}
        onKeyDown={(event) => {
          if (event.key === "Tab") {
            event.currentTarget.hidePopover();
            return;
          }
          if (event.key === "ArrowRight") {
            const buttons = Array.from(
              event.currentTarget.querySelectorAll<HTMLButtonElement>(":scope > button:not(:disabled)"),
            );
            const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
            if (index >= 0 && items[index]?.submenu?.length) {
              event.preventDefault();
              setActiveSubmenuIndex(index);
              setTimeout(() => {
                submenuRef.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
              }, 0);
              return;
            }
          }
          if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const buttons = Array.from(
            event.currentTarget.querySelectorAll<HTMLButtonElement>(":scope > button:not(:disabled)"),
          );
          if (!buttons.length) return;
          const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
          const next =
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? buttons.length - 1
                : (index + (event.key === "ArrowDown" ? 1 : buttons.length - 1)) % buttons.length;
          buttons[next]?.focus();
        }}
      >
        {items.map(
          (
            {
              label: itemLabel,
              run,
              icon,
              checked,
              checkbox,
              reason,
              separator,
              shortcut,
              submenu,
            },
            index,
          ) => {
            if (!itemLabel && separator) {
              return (
                <div
                  key={`separator-${index}`}
                  className="navigator-menu-group-start"
                  role="separator"
                  aria-hidden="true"
                />
              );
            }
            const hasSubmenu = Boolean(submenu && submenu.length > 0);
            return (
              <button
                key={itemLabel}
                type="button"
                role={
                  hasSubmenu
                    ? "menuitem"
                    : checkbox
                      ? "menuitemcheckbox"
                      : checked === undefined
                        ? "menuitem"
                        : "menuitemradio"
                }
                aria-checked={hasSubmenu ? undefined : checked}
                aria-haspopup={hasSubmenu ? "menu" : undefined}
                aria-expanded={hasSubmenu ? activeSubmenuIndex === index : undefined}
                disabled={!run && !hasSubmenu}
                tabIndex={-1}
                title={reason}
                className={separator ? "navigator-menu-group-start" : undefined}
                onPointerEnter={() => {
                  if (hasSubmenu) {
                    setActiveSubmenuIndex(index);
                  } else {
                    setActiveSubmenuIndex(null);
                  }
                }}
                onClick={() => {
                  if (hasSubmenu) {
                    setActiveSubmenuIndex(activeSubmenuIndex === index ? null : index);
                    return;
                  }
                  if (!checkbox) menuRef.current?.hidePopover();
                  run?.();
                }}
              >
                <span className="navigator-menu-icon" aria-hidden="true">
                  {checked ? <Icon name="tick" size={16} /> : icon && <Icon name={icon} size={16} />}
                </span>
                <span>{itemLabel}</span>
                {shortcut && <Keycaps keys={shortcut} />}
                {hasSubmenu && (
                  <span className="navigator-menu-submenu-arrow" aria-hidden="true">
                    <Icon name="chevron-right" size={12} />
                  </span>
                )}
              </button>
            );
          },
        )}
        {activeSubmenuIndex !== null && items[activeSubmenuIndex]?.submenu && (
          <div
            ref={submenuRef}
            className="navigator-menu navigator-submenu"
            role="menu"
            aria-label={items[activeSubmenuIndex].label}
            tabIndex={-1}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft" || event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                const parentIndex = activeSubmenuIndex;
                setActiveSubmenuIndex(null);
                if (parentIndex !== null) {
                  const parentButtons = Array.from(
                    menuRef.current?.querySelectorAll<HTMLButtonElement>(":scope > button") ?? [],
                  );
                  parentButtons[parentIndex]?.focus();
                }
                return;
              }
              if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
              event.preventDefault();
              const buttons = Array.from(
                event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"),
              );
              if (!buttons.length) return;
              const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
              const next =
                event.key === "Home"
                  ? 0
                  : event.key === "End"
                    ? buttons.length - 1
                    : (index + (event.key === "ArrowDown" ? 1 : buttons.length - 1)) % buttons.length;
              buttons[next]?.focus();
            }}
            onPointerLeave={(event) => {
              const next = event.relatedTarget as Node | null;
              if (!menuRef.current?.contains(next)) {
                setActiveSubmenuIndex(null);
              }
            }}
          >
            {items[activeSubmenuIndex].submenu!.map((subItem) => (
              <button
                key={subItem.label}
                type="button"
                role={
                  subItem.checkbox
                    ? "menuitemcheckbox"
                    : subItem.checked === undefined
                      ? "menuitem"
                      : "menuitemradio"
                }
                aria-checked={subItem.checked}
                disabled={!subItem.run}
                tabIndex={-1}
                title={subItem.reason}
                className={subItem.separator ? "navigator-menu-group-start" : undefined}
                onClick={() => {
                  if (!subItem.checkbox) {
                    menuRef.current?.hidePopover();
                    setActiveSubmenuIndex(null);
                  }
                  subItem.run?.();
                }}
              >
                <span className="navigator-menu-icon" aria-hidden="true">
                  {subItem.checked ? (
                    <Icon name="tick" size={16} />
                  ) : (
                    subItem.icon && <Icon name={subItem.icon} size={16} />
                  )}
                </span>
                <span>{subItem.label}</span>
                {subItem.shortcut && <Keycaps keys={subItem.shortcut} />}
              </button>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
