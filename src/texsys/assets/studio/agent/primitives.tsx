/** Agent panel design-system primitives (spec/studio/02-agent-panel-design-system.md). */
import React, {
  useEffect,
  useLayoutEffect,
  useRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import { Icon } from "../ui";
import { t } from "../i18n";

const EDGE = 8;
const GAP = 6;

export function IconButton({
  label,
  icon,
  size = 16,
  active,
  buttonRef,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  icon: string;
  size?: number;
  active?: boolean;
  buttonRef?: React.Ref<HTMLButtonElement>;
}) {
  return (
    <button
      {...props}
      ref={buttonRef}
      type="button"
      className={`ag-icon-button${active ? " is-active" : ""} ${className}`.trim()}
      aria-label={label}
      title={props.title ?? label}
    >
      <Icon name={icon} size={size} />
    </button>
  );
}

/**
 * Anchored floating surface. Prefers the requested side and flips when the
 * viewport is too small; Escape and outside clicks close it and refocus the anchor.
 */
export function Popover({
  anchorRef,
  open,
  onClose,
  onEscape,
  side = "above",
  align = "start",
  label,
  role = "dialog",
  className = "",
  popoverRef,
  children,
}: {
  anchorRef: React.RefObject<HTMLElement | null>;
  open: boolean;
  onClose: () => void;
  /** Return true to consume Escape (e.g. close a nested flyout first). */
  onEscape?: () => boolean;
  side?: "above" | "below";
  align?: "start" | "end";
  label: string;
  role?: string;
  className?: string;
  popoverRef?: React.MutableRefObject<HTMLDivElement | null>;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    const pop = ref.current;
    const anchor = anchorRef.current;
    if (!open || !pop || !anchor) return;
    const place = () => {
      const rect = anchor.getBoundingClientRect();
      pop.style.maxHeight = "";
      const measured = pop.getBoundingClientRect();
      const above = rect.top - GAP - EDGE;
      const below = window.innerHeight - rect.bottom - GAP - EDGE;
      const upward =
        side === "above"
          ? above >= measured.height || above >= below
          : below < measured.height && above > below;
      const room = Math.max(120, upward ? above : below);
      if (measured.height > room) pop.style.maxHeight = `${room}px`;
      const { width, height } = pop.getBoundingClientRect();
      const maxX = Math.max(EDGE, window.innerWidth - width - EDGE);
      const x = align === "end" ? rect.right - width : rect.left;
      pop.style.left = `${Math.max(EDGE, Math.min(x, maxX))}px`;
      pop.style.top = `${upward ? Math.max(EDGE, rect.top - GAP - height) : Math.min(rect.bottom + GAP, window.innerHeight - height - EDGE)}px`;
    };
    place();
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(place) : undefined;
    observer?.observe(pop);
    window.addEventListener("resize", place);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", place);
    };
  }, [open, anchorRef, side, align]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: Event) => {
      const target = event.target as Element | null;
      if (!target) return;
      if (ref.current?.contains(target) || anchorRef.current?.contains(target)) return;
      if (target.closest?.(".ag-flyout")) return;
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      if (onEscape?.()) return;
      onClose();
      anchorRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [open, onClose, onEscape, anchorRef]);

  if (!open) return null;
  return (
    <div
      ref={(node) => {
        ref.current = node;
        if (popoverRef) popoverRef.current = node;
      }}
      role={role}
      aria-label={label}
      translate="no"
      className={`ag-popover notranslate ${className}`.trim()}
    >
      {children}
    </div>
  );
}

/** Side card attached to a popover row: left of the popover, flipping right when cramped. */
export function Flyout({
  popoverRef,
  rowRef,
  label,
  settings,
  children,
}: {
  popoverRef: React.RefObject<HTMLElement | null>;
  rowRef: React.RefObject<HTMLElement | null>;
  label: string;
  settings?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const card = ref.current;
    const pop = popoverRef.current;
    if (!card || !pop) return;
    const place = () => {
      const popRect = pop.getBoundingClientRect();
      const rowRect = rowRef.current?.getBoundingClientRect() ?? popRect;
      const { width, height } = card.getBoundingClientRect();
      let x = popRect.left - GAP - width;
      if (x < EDGE) x = Math.min(popRect.right + GAP, window.innerWidth - width - EDGE);
      const y = Math.max(EDGE, Math.min(rowRect.top - 4, window.innerHeight - height - EDGE));
      card.style.left = `${Math.max(EDGE, x)}px`;
      card.style.top = `${y}px`;
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  });
  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={label}
      translate="no"
      className={`ag-flyout notranslate${settings ? " is-settings" : ""}`}
    >
      {children}
    </div>
  );
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return <div className="ag-menu-label">{children}</div>;
}

export function MenuSeparator() {
  return <div className="ag-menu-separator" role="separator" />;
}

export function MenuItem({
  icon,
  label,
  detail,
  trailing,
  current,
  focused,
  checked,
  rowRef,
  className = "",
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  icon?: string;
  label: ReactNode;
  detail?: ReactNode;
  trailing?: ReactNode;
  current?: boolean;
  focused?: boolean;
  checked?: boolean;
  rowRef?: React.Ref<HTMLButtonElement>;
}) {
  const row = (
    <button
      {...props}
      ref={rowRef}
      type="button"
      role={props.role ?? "option"}
      aria-selected={current ?? checked ?? false}
      className={`ag-menu-item${current ? " is-current" : ""}${focused ? " is-focused" : ""}${trailing ? " has-trailing" : ""} ${className}`.trim()}
    >
      {icon && <Icon name={icon} size={14} />}
      <span className="ag-menu-item-label">{label}</span>
      {detail !== undefined && <span className="ag-menu-item-detail">{detail}</span>}
      {checked && <Icon name="tick" size={14} />}
    </button>
  );
  if (!trailing) return row;
  // Trailing actions are siblings so buttons never nest inside the row button.
  return (
    <div className="ag-menu-row">
      {row}
      <span className="ag-menu-item-trailing">{trailing}</span>
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className="ag-switch"
      onClick={() => onChange(!checked)}
    />
  );
}

export function Chip({
  icon = "file",
  label,
  title,
  off,
  onClick,
  onRemove,
}: {
  icon?: string;
  label: string;
  title?: string;
  off?: boolean;
  onClick?: () => void;
  onRemove?: () => void;
}) {
  return (
    <span
      role="button"
      tabIndex={0}
      className={`ag-chip${off ? " is-off" : ""}`}
      title={title ?? label}
      onClick={onClick}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onClick?.();
        }
      }}
    >
      <Icon name={icon} size={12} />
      <span>{label}</span>
      {onRemove && (
        <span
          role="button"
          tabIndex={-1}
          aria-label={t("移除 {name}", { name: label })}
          className="ag-chip-remove"
          onClick={(event) => {
            event.stopPropagation();
            onRemove();
          }}
        >
          <Icon name="close" size={12} />
        </span>
      )}
    </span>
  );
}

export function PickerButton({
  label,
  buttonRef,
  expanded,
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  label: string;
  expanded: boolean;
  buttonRef?: React.Ref<HTMLButtonElement>;
}) {
  return (
    <button
      {...props}
      ref={buttonRef}
      type="button"
      className="ag-picker"
      aria-haspopup="dialog"
      aria-expanded={expanded}
    >
      <span className="ag-picker-label">{label}</span>
      <Icon name="chevron-down" size={12} />
    </button>
  );
}

export function SendButton({
  busy,
  disabled,
  onSend,
  onStop,
}: {
  busy: boolean;
  disabled: boolean;
  onSend: () => void;
  onStop: () => void;
}) {
  const label = busy ? t("停止") : t("发送");
  return (
    <button
      type="button"
      className="ag-send"
      aria-label={label}
      title={busy ? t("停止（Esc）") : t("发送（Enter）")}
      disabled={!busy && disabled}
      onClick={busy ? onStop : onSend}
    >
      <Icon name={busy ? "stop" : "arrow-up"} size={14} strokeWidth={busy ? 1.5 : 2} />
    </button>
  );
}

export function UsageRing({ used, size }: { used: number; size: number }) {
  const ratio = Math.min(1, Math.max(0, used / size));
  const pct = Math.max(1, Math.round(ratio * 100));
  const r = 5;
  const circumference = 2 * Math.PI * r;
  const tone = pct >= 90 ? " is-danger" : pct >= 75 ? " is-warning" : "";
  const detail = t("上下文已用 {used} / {size} tokens", {
    used: used.toLocaleString(),
    size: size.toLocaleString(),
  });
  return (
    <span className={`ag-usage${tone}`} role="img" aria-label={detail} title={detail}>
      <svg viewBox="0 0 14 14" width={14} height={14} aria-hidden="true">
        <circle className="ag-usage-track" cx="7" cy="7" r={r} />
        <circle
          className="ag-usage-arc"
          cx="7"
          cy="7"
          r={r}
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - Math.max(0.06, ratio))}
        />
      </svg>
      <span>{pct}%</span>
    </span>
  );
}
