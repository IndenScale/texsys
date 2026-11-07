/* SPDX-License-Identifier: MIT
 * Adapted from AtomStudio. Copyright (c) 2026 AtomDocs Contributors.
 * See SOURCE.json and LICENSE for origin and adaptation scope.
 */
import React, { type ReactNode as ComponentChildren, type JSX } from "react";
import { Button, Icon, IconButton, Stack, Text } from "./primitives";
type IconName = string;
export type WorkspaceTab = {
  id: string;
  label: string;
  icon: IconName;
  focused: boolean;
  dirty?: boolean;
  dirtyLabel?: string;
  hasMenu?: boolean;
  action?: {
    label: string;
    icon: IconName;
  };
};

export type InlineTab = {
  id: string;
  label: string;
  focused: boolean;
  disabled?: boolean;
};

export type TabNavigationKey = "ArrowLeft" | "ArrowRight" | "Home" | "End";

export function tabNavigationIndex(
  currentIndex: number,
  key: TabNavigationKey,
  count: number,
): number {
  if (count <= 0) return -1;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return (currentIndex + (key === "ArrowRight" ? 1 : -1) + count) % count;
}

function focusTabFromKeyboard(event: React.KeyboardEvent<HTMLDivElement>): void {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const current = (event.target as Element | null)?.closest<HTMLButtonElement>('[role="tab"]');
  if (!current) return;
  const tabs = Array.from(
    event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]:not(:disabled)'),
  );
  const currentIndex = tabs.indexOf(current);
  if (currentIndex < 0 || !tabs.length) return;
  const nextIndex = tabNavigationIndex(currentIndex, event.key as TabNavigationKey, tabs.length);
  event.preventDefault();
  tabs[nextIndex].focus();
  tabs[nextIndex].click();
}

export function InlineTabStrip(props: {
  className?: string;
  label: string;
  tabs: InlineTab[];
  onSelect: (id: string) => void;
}): JSX.Element {
  return (
    <Stack
      className={`inline-tabs${props.className ? ` ${props.className}` : ""}`}
      role="tablist"
      aria-label={props.label}
      onKeyDown={focusTabFromKeyboard}
    >
      {props.tabs.map((tab) => (
        <Button
          className={`inline-tab${tab.focused ? " is-focused" : ""}`}
          role="tab"
          aria-selected={tab.focused}
          tabIndex={tab.focused ? 0 : -1}
          disabled={tab.disabled}
          tooltip={tab.label}
          onClick={() => props.onSelect(tab.id)}
          key={tab.id}
        >
          <Text>{tab.label}</Text>
        </Button>
      ))}
    </Stack>
  );
}

export function WorkspaceTabStrip(props: {
  className?: string;
  label: string;
  tabs: WorkspaceTab[];
  trailingAction?: ComponentChildren;
  onSelect: (id: string) => void;
  onAction: (id: string) => void;
  onContextMenu?: (event: React.MouseEvent<HTMLElement>, id: string) => void;
  editorGroupId?: string;
  onMoveTab?: (sourceGroupId: string, tabId: string) => void;
}): JSX.Element {
  const tabStopId = (props.tabs.find((tab) => tab.focused) ?? props.tabs[0])?.id ?? null;
  return (
    <Stack
      className={`page-tabs editor-area__tabs${props.className ? ` ${props.className}` : ""}`}
      role="tablist"
      aria-label={props.label}
      onKeyDown={focusTabFromKeyboard}
      onDragOver={(event) => {
        if (props.onMoveTab) event.preventDefault();
      }}
      onDrop={(event) => {
        const payload = event.dataTransfer?.getData("application/x-atomstudio-editor-tab");
        if (!payload || !props.onMoveTab || !props.editorGroupId) return;
        let dragged: { sourceGroupId?: string; tabId?: string };
        try {
          dragged = JSON.parse(payload) as { sourceGroupId?: string; tabId?: string };
        } catch {
          return;
        }
        const { sourceGroupId, tabId } = dragged;
        if (!sourceGroupId || !tabId || sourceGroupId === props.editorGroupId) return;
        event.preventDefault();
        props.onMoveTab(sourceGroupId, tabId);
      }}
    >
      {props.tabs.map((tab) => (
        <Stack
          className={`page-tab${tab.focused ? " is-focused" : ""}`}
          role="presentation"
          key={tab.id}
          draggable={Boolean(props.editorGroupId && props.onMoveTab)}
          onDragStart={(event) => {
            if (!props.editorGroupId || !props.onMoveTab) return;
            event.dataTransfer?.setData(
              "application/x-atomstudio-editor-tab",
              JSON.stringify({ sourceGroupId: props.editorGroupId, tabId: tab.id }),
            );
            if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
          }}
          onContextMenu={
            tab.hasMenu && props.onContextMenu
              ? (event) => props.onContextMenu?.(event, tab.id)
              : undefined
          }
        >
          <Button
            className="page-tab__select"
            role="tab"
            aria-selected={tab.focused}
            aria-haspopup={tab.hasMenu ? "menu" : undefined}
            tabIndex={tab.id === tabStopId ? 0 : -1}
            onClick={() => props.onSelect(tab.id)}
          >
            <Icon name={tab.icon} />
            <Text>{tab.label}</Text>
          </Button>
          {tab.action ? (
            <Stack className="page-tab__aux">
              {tab.dirty ? <Text className="page-tab__dirty" aria-label={tab.dirtyLabel} /> : null}
              <IconButton
                className="page-tab__action"
                icon={tab.action.icon}
                aria-label={tab.action.label}
                tooltipPlacement="bottom"
                onClick={(event) => {
                  // The close button sits inside the tab's selection control.
                  // Without stopping propagation, close removes the Tab and
                  // the bubbling selection immediately opens it again.
                  event.stopPropagation();
                  props.onAction(tab.id);
                }}
              />
            </Stack>
          ) : null}
        </Stack>
      ))}
      {props.trailingAction}
    </Stack>
  );
}
