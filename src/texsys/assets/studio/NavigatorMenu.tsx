import React, { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { NavigatorButton } from "./NavigatorPanel";
import { Icon } from "./ui";

export type NavigatorMenuItem = {
  label: string;
  run?: () => void;
  icon?: string;
  checked?: boolean;
  reason?: string;
  separator?: boolean;
  shortcut?: string;
};

function placeMenu(
  menu: HTMLElement,
  { left, right, top, bottom }: Pick<DOMRect, "left" | "right" | "top" | "bottom">,
  placement: "end" | "side" | "point",
) {
  const edge = 4;
  const gap = 3;
  const { width, height } = menu.getBoundingClientRect();
  const maxX = window.innerWidth - width - edge;
  const maxY = window.innerHeight - height - edge;
  const preferSide = placement === "side" && right <= maxX;
  const x = preferSide ? right : placement === "point" ? left : right - width;
  const y = bottom + gap <= maxY ? bottom + gap : top - height - gap;
  menu.style.left = `${Math.max(edge, Math.min(x, maxX))}px`;
  menu.style.top = `${Math.max(edge, Math.min(y, maxY))}px`;
}

export function NavigatorMenu({
  label,
  title = label,
  items,
  point,
  onDismiss,
  placement = "end",
  triggerClassName,
  triggerIcon = "more",
}: {
  label: string;
  title?: string;
  items: NavigatorMenuItem[];
  point?: { x: number; y: number };
  onDismiss?: () => void;
  placement?: "end" | "side";
  triggerClassName?: string;
  triggerIcon?: ReactNode;
}) {
  const id = useId();
  const menuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);

  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu || !point) return;
    if (!menu.matches(":popover-open")) menu.showPopover();
    placeMenu(menu, { left: point.x, right: point.x, top: point.y, bottom: point.y }, "point");
    (menu.querySelector<HTMLButtonElement>("button:not(:disabled)") ?? menu).focus();
  }, [point]);

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
            placeMenu(menu, event.currentTarget.getBoundingClientRect(), placement);
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
          if (!isOpen) onDismiss?.();
        }}
        onKeyDown={(event) => {
          if (event.key === "Tab") {
            event.currentTarget.hidePopover();
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
      >
        {items.map(({ label: itemLabel, run, icon, checked, reason, separator, shortcut }) => (
          <button
            key={itemLabel}
            type="button"
            role={checked === undefined ? "menuitem" : "menuitemradio"}
            aria-checked={checked}
            disabled={!run}
            tabIndex={-1}
            title={reason}
            className={separator ? "navigator-menu-group-start" : undefined}
            onClick={() => {
              menuRef.current?.hidePopover();
              run?.();
            }}
          >
            <span className="navigator-menu-icon" aria-hidden="true">
              {checked ? <Icon name="tick" size={16} /> : icon && <Icon name={icon} size={16} />}
            </span>
            <span>{itemLabel}</span>
            {shortcut && <kbd>{shortcut}</kbd>}
          </button>
        ))}
      </div>
    </>
  );
}
