import { useSyncExternalStore } from "react";

type WordWrap = "bounded" | "on" | "off";
export type FileEditorMode = "source" | "preview" | "markdown-editor";
type FileEditorKind = "markdown" | "diagram" | "scene" | undefined;
export type FileEditorAssociations = Record<string, FileEditorMode>;

export type UserPreferences = {
  editorFontSize: number;
  editorWordWrap: WordWrap;
  editorLineNumbers: boolean;
  editorMinimap: boolean;
  editorScreenReader: boolean;
  terminalFontSize: number;
  autoSave: boolean;
  confirmBeforeUnload: boolean;
  fileAssociations: FileEditorAssociations;
};

export const fontSizeOptions = Array.from({ length: 17 }, (_, index) => index + 8);
export const defaultEditorPreferences = {
  editorFontSize: 13,
  editorWordWrap: "bounded",
  editorLineNumbers: true,
  editorMinimap: true,
  editorScreenReader: false,
} satisfies Partial<UserPreferences>;

export const defaultTerminalPreferences = { terminalFontSize: 12 };
export const defaultUserPreferences: Readonly<UserPreferences> = {
  ...defaultEditorPreferences,
  ...defaultTerminalPreferences,
  autoSave: false,
  confirmBeforeUnload: true,
  fileAssociations: {},
};
const storageKey = "texsys.studio.user-preferences";

function fontSize(value: unknown, fallback: number) {
  return typeof value === "number" && fontSizeOptions.includes(value) ? value : fallback;
}

export function fileEditorExtension(path: string): string {
  const name = path.slice(path.lastIndexOf("/") + 1);
  return /\.([a-z0-9][a-z0-9_-]*)$/i.exec(name)?.[1].toLowerCase() ?? "";
}

function readFileAssociations(value: unknown): FileEditorAssociations {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter(
      ([extension, mode]) =>
        /^[a-z0-9][a-z0-9_-]*$/.test(extension) &&
        !["constructor", "prototype", "__proto__"].includes(extension) &&
        (mode === "source" ||
          mode === "preview" ||
          (mode === "markdown-editor" && ["md", "markdown"].includes(extension))),
    ),
  );
}

function supportsFileEditor(kind: FileEditorKind, mode: unknown): mode is FileEditorMode {
  return (
    mode === "source" ||
    (mode === "preview" && !!kind) ||
    (mode === "markdown-editor" && kind === "markdown")
  );
}

/** Resolve only when creating a view; existing views keep their selected opening mode. */
export function fileEditorDefault(
  path: string,
  kind: FileEditorKind,
  associations: FileEditorAssociations,
): FileEditorMode {
  const extension = fileEditorExtension(path);
  const requested = Object.hasOwn(associations, extension) ? associations[extension] : undefined;
  if (supportsFileEditor(kind, requested)) return requested;
  return kind && kind !== "markdown" ? "preview" : "source";
}

function readPreferences(raw: string | null): UserPreferences {
  try {
    const saved = JSON.parse(raw ?? "null");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) {
      const { editorWordWrap, editorLineNumbers, editorMinimap } = saved;
      return {
        editorFontSize: fontSize(saved.editorFontSize, defaultUserPreferences.editorFontSize),
        editorWordWrap:
          editorWordWrap === "on" || editorWordWrap === "off"
            ? editorWordWrap
            : defaultUserPreferences.editorWordWrap,
        editorLineNumbers:
          typeof editorLineNumbers === "boolean"
            ? editorLineNumbers
            : defaultUserPreferences.editorLineNumbers,
        editorMinimap:
          typeof editorMinimap === "boolean" ? editorMinimap : defaultUserPreferences.editorMinimap,
        editorScreenReader: saved.editorScreenReader === true,
        terminalFontSize: fontSize(saved.terminalFontSize, defaultUserPreferences.terminalFontSize),
        autoSave:
          typeof saved.autoSave === "boolean" ? saved.autoSave : defaultUserPreferences.autoSave,
        confirmBeforeUnload:
          typeof saved.confirmBeforeUnload === "boolean"
            ? saved.confirmBeforeUnload
            : defaultUserPreferences.confirmBeforeUnload,
        fileAssociations: readFileAssociations(saved.fileAssociations),
      };
    }
  } catch {
    // Malformed browser storage falls back to the existing workbench defaults.
  }
  return defaultUserPreferences;
}

function initialPreferences() {
  try {
    return readPreferences(localStorage.getItem(storageKey));
  } catch {
    return defaultUserPreferences;
  }
}

let preferences = initialPreferences();
const listeners = new Set<() => void>();
const getPreferences = () => preferences;

export function updateUserPreferences(patch: Partial<UserPreferences>) {
  preferences = {
    ...preferences,
    ...patch,
    fileAssociations:
      patch.fileAssociations === undefined
        ? preferences.fileAssociations
        : readFileAssociations(patch.fileAssociations),
  };
  try {
    localStorage.setItem(storageKey, JSON.stringify(preferences));
  } catch {
    // Keep the active page usable when browser storage is unavailable.
  }
  listeners.forEach((listener) => listener());
}

export function setFileEditorDefault(
  path: string,
  kind: FileEditorKind,
  mode: FileEditorMode,
): boolean {
  const extension = fileEditorExtension(path);
  if (
    !extension ||
    !supportsFileEditor(kind, mode) ||
    (mode === "markdown-editor" && !["md", "markdown"].includes(extension))
  )
    return false;
  const fileAssociations = readFileAssociations({
    ...preferences.fileAssociations,
    [extension]: mode,
  });
  if (fileAssociations[extension] !== mode) return false;
  updateUserPreferences({ fileAssociations });
  return true;
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== storageKey && event.key !== null) return;
    preferences = readPreferences(event.key === null ? null : event.newValue);
    listeners.forEach((listener) => listener());
  });
}

export function useUserPreferences() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getPreferences,
    () => defaultUserPreferences,
  );
}
