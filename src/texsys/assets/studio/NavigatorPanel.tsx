import React, { type ButtonHTMLAttributes, type HTMLAttributes, type ReactNode } from "react";
import { Icon } from "./ui";
import "./navigator.css";

export function NavigatorPanel({
  title,
  actions,
  count,
  className = "",
  children,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  title?: string;
  actions?: ReactNode;
  count?: number;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div {...props} className={`navigator-panel ${className}`}>
      <header className="navigator-heading">
        <strong title={title}>{title}</strong>
        {count !== undefined && <span>{count}</span>}
        {actions && <NavigatorToolbar>{actions}</NavigatorToolbar>}
      </header>
      {children}
    </div>
  );
}

export function NavigatorPage({ className = "", ...props }: HTMLAttributes<HTMLElement>) {
  return <section {...props} className={`navigator-page ${className}`} />;
}

export function NavigatorToolbar({ className = "", ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div {...props} className={`navigator-toolbar ${className}`} />;
}

export function NavigatorButton({
  label,
  title = label,
  icon,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; icon: ReactNode }) {
  return (
    <button
      {...props}
      type="button"
      className={`navigator-button ${className}`}
      aria-label={label}
      title={title}
    >
      {typeof icon === "string" ? <Icon name={icon} size={16} /> : icon}
    </button>
  );
}
