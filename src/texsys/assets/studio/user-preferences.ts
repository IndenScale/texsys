import { useSyncExternalStore } from "react";

type WordWrap = "bounded" | "on" | "off";

export type UserPreferences = {
  editorFontSize: number;
  editorWordWrap: WordWrap;
  editorLineNumbers: boolean;
  editorMinimap: boolean;
  terminalFontSize: number;
  autoSave: boolean;
  confirmBeforeUnload: boolean;
};

export const fontSizeOptions = Array.from({ length: 17 }, (_, index) => index + 8);
export const defaultEditorPreferences = {
  editorFontSize: 13,
  editorWordWrap: "bounded",
  editorLineNumbers: true,
  editorMinimap: true,
} satisfies Partial<UserPreferences>;

export const defaultTerminalPreferences = { terminalFontSize: 12 };
const defaults = {
  ...defaultEditorPreferences,
  ...defaultTerminalPreferences,
  autoSave: false,
  confirmBeforeUnload: true,
};
const storageKey = "texsys.studio.user-preferences";

function fontSize(value: unknown, fallback: number) {
  return typeof value === "number" && fontSizeOptions.includes(value) ? value : fallback;
}

function readPreferences(raw: string | null): UserPreferences {
  try {
    const saved = JSON.parse(raw ?? "null");
    if (saved && typeof saved === "object") {
      const { editorWordWrap, editorLineNumbers, editorMinimap } = saved;
      return {
        editorFontSize: fontSize(saved.editorFontSize, defaults.editorFontSize),
        editorWordWrap:
          editorWordWrap === "on" || editorWordWrap === "off"
            ? editorWordWrap
            : defaults.editorWordWrap,
        editorLineNumbers:
          typeof editorLineNumbers === "boolean" ? editorLineNumbers : defaults.editorLineNumbers,
        editorMinimap: typeof editorMinimap === "boolean" ? editorMinimap : defaults.editorMinimap,
        terminalFontSize: fontSize(saved.terminalFontSize, defaults.terminalFontSize),
        autoSave: saved.autoSave === true,
        confirmBeforeUnload: saved.confirmBeforeUnload !== false,
      };
    }
  } catch {
    // Malformed browser storage falls back to the existing workbench defaults.
  }
  return defaults;
}

function initialPreferences() {
  try {
    return readPreferences(localStorage.getItem(storageKey));
  } catch {
    return defaults;
  }
}

let preferences = initialPreferences();
const listeners = new Set<() => void>();
const getPreferences = () => preferences;

export function updateUserPreferences(patch: Partial<UserPreferences>) {
  preferences = { ...preferences, ...patch };
  try {
    localStorage.setItem(storageKey, JSON.stringify(preferences));
  } catch {
    // Keep the active page usable when browser storage is unavailable.
  }
  listeners.forEach((listener) => listener());
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
    () => defaults,
  );
}
