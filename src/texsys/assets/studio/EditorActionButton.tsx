import React, { type ButtonHTMLAttributes } from "react";
import { ToolbarButton } from "./ToolbarButton";
import { Icon } from "./ui";
import "./editor-action.css";

type EditorActionProps = Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "aria-label" | "children" | "title"
> & {
  label: string;
  icon: string;
  emphasis?: boolean;
};

/** An editor command with a shared icon and a localized name, without a visible text label. */
export function EditorActionButton({
  label,
  icon,
  emphasis,
  className = "",
  type = "button",
  ...props
}: EditorActionProps) {
  return (
    <ToolbarButton
      {...props}
      type={type}
      className={`editor-action${emphasis ? " editor-action--emphasis" : ""}${className ? ` ${className}` : ""}`}
      aria-label={label}
      tooltip={label}
      title={props.disabled ? label : undefined}
    >
      <Icon name={icon} size={16} />
    </ToolbarButton>
  );
}
