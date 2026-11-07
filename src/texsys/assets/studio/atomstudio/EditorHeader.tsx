/* SPDX-License-Identifier: MIT
 * Adapted from AtomStudio. Copyright (c) 2026 AtomDocs Contributors.
 * See SOURCE.json and LICENSE for origin and adaptation scope.
 */
import type { ReactNode as ComponentChildren, JSX } from "react";
import { Stack } from "./primitives";

export function EditorHeader(props: {
  children: ComponentChildren;
  className?: string;
}): JSX.Element {
  return (
    <Stack
      className={`editor-header editor-area__header${props.className ? ` ${props.className}` : ""}`}
    >
      <Stack className="editor-header__breadcrumb">{props.children}</Stack>
    </Stack>
  );
}
