/* SPDX-License-Identifier: MIT
 * Adapted from AtomStudio. Copyright (c) 2026 AtomDocs Contributors.
 * See SOURCE.json and LICENSE for origin and adaptation scope.
 */
import type { ReactNode as ComponentChildren, JSX } from "react";
import { Stack } from "./primitives";
import { EditorCanvas } from "./EditorCanvas";
import { EditorHeader } from "./EditorHeader";
import { EditorTabBar } from "./EditorTabBar";

export type EditorAreaChrome =
  "tabs-and-breadcrumb" | "tabs-only" | "breadcrumb-only" | "content-only";
export type EditorAreaLayout = "contained" | "full-bleed";

/**
 * Stable workbench container for every Studio page.
 *
 * Unified Editor Area structure:
 * 1. Editor Tab Bar (document tabs; global layout actions live in the host header)
 * 2. Editor Header (resource context and domain actions)
 * 3. Canvas (viewport for surface rendering)
 */
export function EditorArea(props: {
  tabs: ComponentChildren;
  breadcrumb?: ComponentChildren;
  headerActions?: ComponentChildren;
  chrome: EditorAreaChrome;
  layout: EditorAreaLayout;
  className?: string;
  children: ComponentChildren;
}): JSX.Element {
  const showTabs = props.chrome === "tabs-and-breadcrumb" || props.chrome === "tabs-only";
  const showHeader = props.chrome === "tabs-and-breadcrumb" || props.chrome === "breadcrumb-only";

  return (
    <Stack
      className={`editor-area editor-area--chrome-${props.chrome} editor-area--layout-${props.layout}${
        props.className ? ` ${props.className}` : ""
      }`}
    >
      {showTabs ? <EditorTabBar>{props.tabs}</EditorTabBar> : null}
      {showHeader && props.breadcrumb ? (
        <EditorHeader actions={props.headerActions}>{props.breadcrumb}</EditorHeader>
      ) : null}
      <EditorCanvas layout={props.layout}>{props.children}</EditorCanvas>
    </Stack>
  );
}
