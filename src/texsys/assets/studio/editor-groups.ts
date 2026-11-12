import type { TextFormat, TextFormatChange } from "./sdk";
import type { SearchMatch } from "./workspace-model";
import type { PreviewViewport } from "./preview-viewport";

export type EditorPresentation = "source" | "preview" | "markdown-editor";
export type SourceEditorStatus = {
  line: number;
  column: number;
  tabSize: number;
  insertSpaces: boolean;
  language: string;
  languageOverride: string;
  selected: number;
  selections: number;
  format?: TextFormat;
  formatEditable?: boolean;
};

export type SourceStatusAction =
  | { kind: "indent"; tabSize?: number; insertSpaces?: boolean }
  | { kind: "convertIndent"; insertSpaces: boolean }
  | { kind: "language"; language: string }
  | { kind: "format"; change: TextFormatChange };

export const sourceLanguages = {
  auto: "自动检测",
  plaintext: "纯文本",
  json: "JSON",
  markdown: "Markdown",
  ini: "INI",
  toml: "TOML",
  python: "Python",
  shell: "Shell",
  yaml: "YAML",
} as const;

export type EditorView = {
  path: string;
  presentation?: EditorPresentation;
  location?: SearchMatch;
  viewport?: PreviewViewport;
};
export type EditorGroup = { id: string; views: EditorView[]; activePath: string | null };
export type EditorGroupTab = {
  id: string;
  label: string;
  description?: string;
  icon?: string;
  dirty?: boolean;
  selected?: boolean;
  select: () => void;
  close: () => boolean | Promise<boolean>;
};

/** Wait for each editor's normal close workflow; cancellation leaves the rest open. */
export async function closeEditorsInOrder(tabs: readonly EditorGroupTab[]): Promise<boolean> {
  for (const tab of tabs) {
    if (!(await tab.close())) return false;
  }
  return true;
}
export const primaryEditorGroup = "primary";
export const secondaryEditorGroup = "secondary";
export const emptyEditorGroup = (id = primaryEditorGroup): EditorGroup => ({
  id,
  views: [],
  activePath: null,
});
export function openEditorView(
  groups: EditorGroup[],
  groupId: string,
  path: string | null,
  patch: Partial<Omit<EditorView, "path">> = {},
): EditorGroup[] {
  const existing = groups.some((group) => group.id === groupId)
    ? groups
    : [...groups, emptyEditorGroup(groupId)];
  return existing.map((group) => {
    if (group.id !== groupId) return group;
    return {
      ...group,
      activePath: path,
      views:
        path === null
          ? group.views
          : group.views.some((view) => view.path === path)
            ? group.views.map((view) => (view.path === path ? { ...view, ...patch } : view))
            : [...group.views, { path, ...patch }],
    };
  });
}
export function updateEditorView(
  groups: EditorGroup[],
  groupId: string,
  path: string,
  patch: Partial<Omit<EditorView, "path">>,
): EditorGroup[] {
  return groups.map((group) =>
    group.id === groupId
      ? {
          ...group,
          views: group.views.map((view) => (view.path === path ? { ...view, ...patch } : view)),
        }
      : group,
  );
}
export function closeEditorView(
  groups: EditorGroup[],
  groupId: string,
  path: string,
): EditorGroup[] {
  return groups
    .map((group) => {
      if (group.id !== groupId) return group;
      const index = group.views.findIndex((view) => view.path === path);
      const views = group.views.filter((view) => view.path !== path);
      return {
        ...group,
        views,
        activePath:
          group.activePath === path
            ? ((views[index] ?? views[index - 1])?.path ?? null)
            : group.activePath,
      };
    })
    .filter((group) => group.id === primaryEditorGroup || group.views.length > 0);
}
export function removeEditorDocument(groups: EditorGroup[], path: string): EditorGroup[] {
  return groups.reduce((next, group) => closeEditorView(next, group.id, path), groups);
}
export function editorViewCount(groups: EditorGroup[], path: string): number {
  return groups.reduce(
    (count, group) => count + Number(group.views.some((view) => view.path === path)),
    0,
  );
}

export function renameEditorDocument(
  groups: EditorGroup[],
  previousPath: string,
  nextPath: string,
): EditorGroup[] {
  return groups.map((group) => ({
    ...group,
    activePath: group.activePath === previousPath ? nextPath : group.activePath,
    views: group.views.map((view) =>
      view.path === previousPath ? { ...view, path: nextPath, location: undefined } : view,
    ),
  }));
}

export function resolvedEditorPresentation(
  kind: "markdown" | "diagram" | "scene" | "svg" | undefined,
  requested?: EditorPresentation,
): EditorPresentation {
  if (!kind || (requested === "markdown-editor" && kind !== "markdown")) return "source";
  return requested ?? (kind === "markdown" ? "source" : "preview");
}

/** Capture the preference once. Reopening an existing view retains its explicit opener. */
export function initializeEditorView(
  groups: EditorGroup[],
  groupId: string,
  path: string,
  presentation: EditorPresentation,
): EditorGroup[] {
  return groups.map((group) =>
    group.id === groupId
      ? {
          ...group,
          views: group.views.map((view) =>
            view.path === path && !view.presentation ? { ...view, presentation } : view,
          ),
        }
      : group,
  );
}
