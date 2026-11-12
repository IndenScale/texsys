/* SPDX-License-Identifier: MIT
 * React boundary for the imported AtomStudio components.
 */
import React, {
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type Ref,
  type SelectHTMLAttributes,
} from "react";
import { Icon as StudioIcon } from "../ui";
import "./controls.css";
type Props = HTMLAttributes<HTMLDivElement>;
export function Stack(props: Props) {
  return <div {...props} />;
}
export function Separator(props: Props) {
  return <div role="separator" {...props} />;
}
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  tooltip?: string;
  tooltipPlacement?: string;
};
export function Button({
  tooltip,
  tooltipPlacement: _,
  appearance,
  density = "default",
  className,
  ...props
}: ButtonProps & { appearance?: "primary" | "secondary"; density?: "default" | "compact" }) {
  return (
    <button
      title={tooltip}
      {...props}
      className={
        appearance ? `studio-control studio-control-${appearance} ${className ?? ""}` : className
      }
      data-density={appearance ? density : undefined}
    />
  );
}

export function Select({
  width = "standard",
  className,
  controlRef,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & {
  width?: "standard" | "wide";
  controlRef?: Ref<HTMLSelectElement>;
}) {
  return (
    <span className={`studio-select ${className ?? ""}`} data-width={width}>
      <select {...props} ref={controlRef} className="studio-control studio-select-input" />
      <StudioIcon name="chevron-down" size={14} />
    </span>
  );
}

export function Icon({ name }: { name: string }) {
  return <StudioIcon name={name} size={16} />;
}
export function IconButton({
  icon,
  tooltip,
  tooltipPlacement: _,
  ...props
}: ButtonProps & { icon: string }) {
  return (
    <button
      {...props}
      className={`icon-button ${props.className ?? ""}`}
      title={tooltip ?? props["aria-label"]}
    >
      <Icon name={icon} />
    </button>
  );
}
export function Text(props: React.HTMLAttributes<HTMLSpanElement>) {
  return <span {...props} />;
}
