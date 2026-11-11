import React from "react";
import { EditorActionButton } from "./EditorActionButton";
import { NavigatorMenu, type NavigatorMenuItem } from "./NavigatorMenu";
import { t, useLocale } from "./i18n";
import { Icon } from "./ui";
import type { filePreviewKind } from "./workspace-model";
import "./file-editor-actions.css";

export type FilePreviewKind = NonNullable<ReturnType<typeof filePreviewKind>>;
export type FilePresentation = "source" | "preview" | "markdown-editor";

const previewLabels: Record<FilePreviewKind, string> = {
  markdown: "Markdown 预览",
  diagram: "原理图预览",
  scene: "场景预览",
};

export function FileOpenWith({
  path,
  kind,
  presentation,
  defaultPresentation,
  onChange,
  onSetDefault,
}: {
  path: string;
  kind?: FilePreviewKind;
  presentation: FilePresentation;
  defaultPresentation?: FilePresentation;
  onChange: (mode: FilePresentation) => void;
  onSetDefault: (mode: FilePresentation) => void;
}) {
  useLocale();
  const openers: { mode: FilePresentation; label: string }[] = [
    { mode: "source", label: t("文本编辑器") },
  ];
  if (kind) openers.push({ mode: "preview", label: t(previewLabels[kind]) });
  if (kind === "markdown") openers.push({ mode: "markdown-editor", label: t("Markdown 编辑器") });
  const selected = openers.find(({ mode }) => mode === presentation) ?? openers[0];
  const defaultOpener =
    openers.find(({ mode }) => mode === defaultPresentation) ??
    openers[kind && kind !== "markdown" ? 1 : 0];
  const items: NavigatorMenuItem[] = openers.map(({ mode, label }) => ({
    label,
    checked: selected.mode === mode,
    run: () => onChange(mode),
  }));
  const extension = /\.([^./]+)$/.exec(path)?.[1]?.toLowerCase();
  if (kind && extension) {
    items.push({
      label: t("为「{0}」设置默认值", { 0: `*.${extension}` }),
      separator: true,
      submenu: openers.map(({ mode, label }) => ({
        label,
        checked: defaultOpener.mode === mode,
        run: () => onSetDefault(mode),
      })),
    });
  }
  return (
    <NavigatorMenu
      label={t("打开方式")}
      items={items}
      triggerClassName="file-open-with-trigger"
      triggerIcon={
        <>
          <span>{selected.label}</span>
          <Icon name="chevron" size={16} />
        </>
      }
    />
  );
}

function disableMenuItems(items: NavigatorMenuItem[]): NavigatorMenuItem[] {
  return items.map((item) => ({
    ...item,
    run: undefined,
    submenu: item.submenu ? disableMenuItems(item.submenu) : undefined,
  }));
}

export function FileEditorActions({
  kind,
  presentation,
  onPreview,
  onPreviewBeside,
  onSplit,
  items,
  disabled = false,
}: {
  kind?: FilePreviewKind;
  presentation: FilePresentation;
  onPreview: () => void;
  onPreviewBeside: () => void;
  onSplit: () => void;
  items: NavigatorMenuItem[];
  disabled?: boolean;
}) {
  useLocale();
  const showingPreview = !!kind && presentation === "preview";
  return (
    <>
      {kind && (
        <EditorActionButton
          label={t(showingPreview ? "源代码" : "预览")}
          icon={showingPreview ? "code" : "preview"}
          disabled={disabled}
          onClick={onPreview}
        />
      )}
      {kind && !showingPreview && (
        <EditorActionButton
          label={t("分栏预览")}
          icon="preview-beside"
          disabled={disabled}
          onClick={onPreviewBeside}
        />
      )}
      <EditorActionButton
        label={t("分栏")}
        icon="split-horizontal"
        disabled={disabled}
        onClick={onSplit}
      />
      <NavigatorMenu
        label={t("更多操作")}
        items={disabled ? disableMenuItems(items) : items}
        triggerClassName="file-editor-more-action"
      />
    </>
  );
}
