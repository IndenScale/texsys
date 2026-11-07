/* SPDX-License-Identifier: MIT
 * Adapted from AtomStudio. Copyright (c) 2026 AtomDocs Contributors.
 * See SOURCE.json and LICENSE for origin and adaptation scope.
 */
import type { ReactNode as ComponentChildren, JSX } from "react";
import { Stack } from "./primitives";

export function EditorCanvas(props: {
  children: ComponentChildren;
  layout?: "contained" | "full-bleed";
  className?: string;
}): JSX.Element {
  return (
    <Stack
      className={`editor-canvas editor-area__canvas${
        props.layout ? ` editor-canvas--layout-${props.layout}` : ""
      }${props.className ? ` ${props.className}` : ""}`}
    >
      <Stack className="editor-canvas__content">{props.children}</Stack>
    </Stack>
  );
}
