/* SPDX-License-Identifier: MIT
 * React boundary for the imported AtomStudio components.
 */
import React, { type HTMLAttributes, type ButtonHTMLAttributes } from "react";
import { Icon as StudioIcon } from "../ui";
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
export function Button({ tooltip, tooltipPlacement: _, ...props }: ButtonProps) {
  return <button title={tooltip} {...props} />;
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
