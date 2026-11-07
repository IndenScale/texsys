import type { StudioLayout } from "./sdk";
import {
  MIN_BOTTOM_HEIGHT,
  MIN_INSPECTOR_WIDTH,
  MIN_SIDEBAR_WIDTH,
} from "./atomstudio/layoutSizing";

export type LayoutPreferences = {
  discipline: string;
  layouts: Record<string, StudioLayout>;
};

const storageKey = (project: string) => `texsys.studio.layout:${project}`;

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function dimension(value: unknown, minimum: number, fallback: number) {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum
    ? value
    : fallback;
}

export function readLayoutPreferences(project: string, defaults: StudioLayout): LayoutPreferences {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(storageKey(project)) ?? "null");
    if (record(saved) && record(saved.layouts)) {
      return {
        discipline: typeof saved.discipline === "string" ? saved.discipline : "",
        layouts: Object.fromEntries(
          Object.entries(saved.layouts).flatMap(([id, layout]) => {
            if (!record(layout)) return [];
            return [
              [
                id,
                {
                  sidebarOpen:
                    typeof layout.sidebarOpen === "boolean"
                      ? layout.sidebarOpen
                      : defaults.sidebarOpen,
                  inspectorOpen:
                    typeof layout.inspectorOpen === "boolean"
                      ? layout.inspectorOpen
                      : defaults.inspectorOpen,
                  bottomOpen:
                    typeof layout.bottomOpen === "boolean"
                      ? layout.bottomOpen
                      : defaults.bottomOpen,
                  bottomTab:
                    layout.bottomTab === "tasks" || layout.bottomTab === "problems"
                      ? layout.bottomTab
                      : defaults.bottomTab,
                  sidebarWidth: dimension(
                    layout.sidebarWidth,
                    MIN_SIDEBAR_WIDTH,
                    defaults.sidebarWidth,
                  ),
                  inspectorWidth: dimension(
                    layout.inspectorWidth,
                    MIN_INSPECTOR_WIDTH,
                    defaults.inspectorWidth,
                  ),
                  bottomHeight: dimension(
                    layout.bottomHeight,
                    MIN_BOTTOM_HEIGHT,
                    defaults.bottomHeight,
                  ),
                  sidebarActivity:
                    typeof layout.sidebarActivity === "string" ? layout.sidebarActivity : undefined,
                },
              ],
            ];
          }),
        ),
      };
    }
  } catch {
    // Use the defaults if preferences are unreadable or storage is unavailable.
  }
  return { discipline: "", layouts: {} };
}

export function writeLayoutPreferences(project: string, preferences: LayoutPreferences) {
  try {
    localStorage.setItem(storageKey(project), JSON.stringify(preferences));
  } catch {
    // Layout controls still work for the current page when storage is unavailable.
  }
}
