import React, { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Keycaps } from "./Keycaps";
import { Button } from "./atomstudio/primitives";
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
  menu.style.maxHeight = "";
  menu.style.overflowY = "";
  const { width, height } = menu.getBoundingClientRect();
  const maxX = window.innerWidth - width - edge;
  const x =
    placement === "side" && right <= maxX
      ? right
      : placement === "start" || placement === "point"
        ? left
        : right - width;
  const above = Math.max(0, top - gap - edge);
  const below = Math.max(0, window.innerHeight - bottom - gap - edge);
  const upward =
    direction === "up"
      ? height <= above || (height > below && above >= below)
      : height > below && (height <= above || above > below);
  const available =
    placement === "point" && direction !== "up"
      ? window.innerHeight - 2 * edge
      : upward
        ? above
        : below;
  if (height > available) {
    menu.style.maxHeight = `${available}px`;
    menu.style.overflowY = "auto";
  }
  const actualHeight = menu.getBoundingClientRect().height;
  const y =
    placement === "point" && direction !== "up"
      ? Math.min(top, window.innerHeight - actualHeight - edge)
      : upward
        ? top - gap - actualHeight
        : bottom + gap;
  menu.style.left = `${Math.max(edge, Math.min(x, maxX))}px`;
  menu.style.top = `${Math.max(edge, y)}px`;
}

export function NavigatorMenu({
  label,
  title = label,
  items,
  point,
  onDismiss,
  onOpen,
  placement = "end",
  direction = "auto",
  triggerClassName,
  triggerIcon = "more",
  menuClassName,
  openRequest,
  triggerDisabled,
  detail,
}: {
  label: string;
  title?: string;
  items: NavigatorMenuItem[];
  point?: { x: number; y: number };
  onDismiss?: () => void;
  onOpen?: () => void;
  placement?: "end" | "start" | "side";
  direction?: "down" | "up" | "auto";
  triggerClassName?: string;
  triggerIcon?: ReactNode;
  menuClassName?: string;
  openRequest?: object;
  triggerDisabled?: boolean;
  detail?: ReactNode;
}) {
  const id = useId();
  const menuRef = useRef<HTMLDivElement>(null);
  const submenuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [activeSubmenuIndex, setActiveSubmenuIndex] = useState<number | null>(null);
  const submenuCloseTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const focusSubmenuAfterOpen = useRef(false);
  const cancelSubmenuClose = () => {
    clearTimeout(submenuCloseTimer.current);
    submenuCloseTimer.current = undefined;
  };
  const closeSubmenu = () => {
    cancelSubmenuClose();
    focusSubmenuAfterOpen.current = false;
    setActiveSubmenuIndex(null);
  };
  const leaveSubmenu = () => {
    cancelSubmenuClose();
    // Preserve the submenu while the pointer crosses the gap between the two menus.
    submenuCloseTimer.current = setTimeout(closeSubmenu, 160);
  };
  const openSubmenu = (index: number, focus = false) => {
    cancelSubmenuClose();
    focusSubmenuAfterOpen.current = focus;
    setActiveSubmenuIndex(index);
    if (focus && activeSubmenuIndex === index) {
      submenuRef.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
      focusSubmenuAfterOpen.current = false;
    }
  };
  const submenuParent = (index: number) =>
    menuRef.current?.querySelector<HTMLButtonElement>(
      `:scope > button[data-menu-index="${index}"]`,
    );
  useEffect(() => () => clearTimeout(submenuCloseTimer.current), []);

  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu || !point) return;
    if (!menu.matches(":popover-open")) menu.showPopover();
    placeMenu(
      menu,
      { left: point.x, right: point.x, top: point.y, bottom: point.y },
      "point",
      direction,
    );
    (menu.querySelector<HTMLButtonElement>("button:not(:disabled)") ?? menu).focus({
      preventScroll: true,
    });
  }, [point, direction]);

  useLayoutEffect(() => {
    const sub = submenuRef.current;
    if (!sub || activeSubmenuIndex === null || !items[activeSubmenuIndex]?.submenu) return;
    const parentMenu = menuRef.current;
    if (!parentMenu) return;
    const button = submenuParent(activeSubmenuIndex);
    if (!button) return;
    const rect = button.getBoundingClientRect();
    const edge = 6;
    sub.style.maxHeight = `${window.innerHeight - edge * 2}px`;
    sub.style.overflowY = "auto";
    const { width, height } = sub.getBoundingClientRect();
    const parent = parentMenu.getBoundingClientRect();
    const gap = 2;
    const spaceRight = window.innerWidth - parent.right - edge - gap;
    const spaceLeft = parent.left - edge - gap;
    if (spaceRight >= width || spaceLeft >= width) {
      sub.style.left = `${spaceRight >= width ? parent.right + gap : parent.left - gap - width}px`;
      sub.style.top = `${Math.max(edge, Math.min(rect.top - 4, window.innerHeight - height - edge))}px`;
    } else {
      placeMenu(sub, rect, "start");
    }
    if (focusSubmenuAfterOpen.current) {
      sub.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
      focusSubmenuAfterOpen.current = false;
    }
  }, [activeSubmenuIndex, items]);

  useEffect(() => {
    if (!open) return;
    const dismiss = () => {
      if (menuRef.current?.matches(":popover-open")) menuRef.current.hidePopover();
    };
    const dismissOnOutsideScroll = (event: Event) => {
      if (!(event.target instanceof Node) || !menuRef.current?.contains(event.target)) dismiss();
    };
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismissOnOutsideScroll, true);
    return () => {
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismissOnOutsideScroll, true);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (openRequest && !menuRef.current?.matches(":popover-open")) {
      document.getElementById(`${id}-trigger`)?.click();
    }
  }, [openRequest, id]);

  useLayoutEffect(() => {
    const menu = menuRef.current;
    const trigger = document.getElementById(`${id}-trigger`);
    if (open && menu && trigger) {
      placeMenu(menu, trigger.getBoundingClientRect(), placement, direction);
    }
  }, [open, id, items, placement, direction]);

  return (
    <>
      {!point && (
        <NavigatorButton
          id={`${id}-trigger`}
          disabled={triggerDisabled}
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
            (menu.querySelector<HTMLButtonElement>("button:not(:disabled)") ?? menu).focus({
              preventScroll: true,
            });
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
        className={`navigator-menu ${menuClassName ?? ""}`}
        id={id}
        ref={menuRef}
        role="menu"
        aria-label={label}
        popover="auto"
        tabIndex={-1}
        onToggle={(event) => {
          const isOpen = event.newState === "open";
          setOpen(isOpen);
          if (isOpen) onOpen?.();
          else {
            closeSubmenu();
            onDismiss?.();
          }
        }}
        onPointerLeave={(event) => {
          const next = event.relatedTarget instanceof Node ? event.relatedTarget : null;
          if (!submenuRef.current?.contains(next)) leaveSubmenu();
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            event.currentTarget.hidePopover();
            document.getElementById(`${id}-trigger`)?.focus();
            return;
          }
          if (event.key === "Tab") {
            event.currentTarget.hidePopover();
            return;
          }
          if (event.key === "ArrowRight") {
            const focused = document.activeElement as HTMLElement | null;
            const index = Number(focused?.dataset.menuIndex);
            if (focused?.parentElement === event.currentTarget && items[index]?.submenu?.length) {
              event.preventDefault();
              event.stopPropagation();
              openSubmenu(index, true);
              return;
            }
          }
          if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const buttons = Array.from(
            event.currentTarget.querySelectorAll<HTMLButtonElement>(
              ":scope > button:not(:disabled)",
            ),
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
        {detail && <div className="navigator-menu-detail">{detail}</div>}
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
                data-menu-index={index}
                aria-checked={hasSubmenu ? undefined : checked}
                aria-haspopup={hasSubmenu ? "menu" : undefined}
                aria-expanded={hasSubmenu ? activeSubmenuIndex === index : undefined}
                disabled={!run && !hasSubmenu}
                tabIndex={-1}
                title={reason}
                className={separator ? "navigator-menu-group-start" : undefined}
                onPointerEnter={() => {
                  if (hasSubmenu) openSubmenu(index);
                  else closeSubmenu();
                }}
                onFocus={() => {
                  if (activeSubmenuIndex !== index) closeSubmenu();
                }}
                onClick={() => {
                  if (hasSubmenu) {
                    openSubmenu(index, true);
                    return;
                  }
                  if (!checkbox) {
                    menuRef.current?.hidePopover();
                    document.getElementById(`${id}-trigger`)?.focus({ preventScroll: true });
                  }
                  run?.();
                }}
              >
                <span className="navigator-menu-icon" aria-hidden="true">
                  {checked ? (
                    <Icon name="tick" size={16} />
                  ) : (
                    icon && <Icon name={icon} size={16} />
                  )}
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
            className={`navigator-menu navigator-submenu ${menuClassName ?? ""}`}
            role="menu"
            aria-label={items[activeSubmenuIndex].label}
            tabIndex={-1}
            onPointerEnter={cancelSubmenuClose}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft" || event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                const parentIndex = activeSubmenuIndex;
                closeSubmenu();
                if (parentIndex !== null) submenuParent(parentIndex)?.focus();
                return;
              }
              if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
              event.preventDefault();
              event.stopPropagation();
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
                    : (index + (event.key === "ArrowDown" ? 1 : buttons.length - 1)) %
                      buttons.length;
              buttons[next]?.focus();
            }}
            onPointerLeave={(event) => {
              const next = event.relatedTarget instanceof Node ? event.relatedTarget : null;
              if (!menuRef.current?.contains(next)) {
                leaveSubmenu();
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
                    closeSubmenu();
                    document.getElementById(`${id}-trigger`)?.focus({ preventScroll: true });
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

export function NavigatorSplitButton({
  label,
  menuLabel,
  items,
  onClick,
  disabled,
  appearance = "secondary",
}: {
  label: string;
  menuLabel: string;
  items: NavigatorMenuItem[];
  onClick: () => void;
  disabled?: boolean;
  appearance?: "primary" | "secondary";
}) {
  return (
    <div
      className="studio-split-button"
      data-appearance={appearance}
      data-disabled={disabled || undefined}
    >
      <Button
        type="button"
        appearance={appearance}
        density="compact"
        disabled={disabled}
        onClick={onClick}
      >
        {label}
      </Button>
      <NavigatorMenu
        label={menuLabel}
        items={items}
        placement="start"
        triggerIcon="chevron-down"
        triggerClassName="studio-split-trigger"
        menuClassName="navigator-menu-compact"
        triggerDisabled={disabled}
      />
    </div>
  );
}
