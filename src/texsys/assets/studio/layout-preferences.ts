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
const titlebarStorageKey = "texsys.studio.titlebar.hidden";

export const titlebarGroups = [
  { id: "commandCenter", label: "命令中心" },
  { id: "navigationControls", label: "导航控件" },
  { id: "layoutControls", label: "布局控件" },
] as const;

export type TitlebarGroup = (typeof titlebarGroups)[number]["id"];

export function readHiddenTitlebarGroups(): TitlebarGroup[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(titlebarStorageKey) ?? "null");
    if (Array.isArray(saved))
      return titlebarGroups.map(({ id }) => id).filter((id) => saved.includes(id));
  } catch {
    // Use the defaults if preferences are unreadable or storage is unavailable.
  }
  return [];
}

export function writeHiddenTitlebarGroups(hidden: TitlebarGroup[]) {
  try {
    localStorage.setItem(titlebarStorageKey, JSON.stringify(hidden));
  } catch {
    // The title bar still works for the current page when storage is unavailable.
  }
}

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
                    typeof layout.bottomTab === "string" && layout.bottomTab
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
                  ...(Array.isArray(layout.hiddenActivities) && {
                    hiddenActivities: layout.hiddenActivities.filter(
                      (id): id is string => typeof id === "string",
                    ),
                  }),
                  ...(Array.isArray(layout.hiddenBottomTabs) && {
                    hiddenBottomTabs: layout.hiddenBottomTabs.filter(
                      (id): id is string => typeof id === "string",
                    ),
                  }),
                  ...(record(layout.hideBadges) && {
                    hideBadges: Object.fromEntries(
                      Object.entries(layout.hideBadges).filter(
                        (entry): entry is [string, boolean] => typeof entry[1] === "boolean",
                      ),
                    ),
                  }),
                  ...(typeof layout.panelWidth === "number" && {
                    panelWidth: dimension(layout.panelWidth, 200, defaults.panelWidth ?? 320),
                  }),
                  ...(typeof layout.panelPosition === "string" &&
                    ["bottom", "left", "right"].includes(layout.panelPosition) && {
                      panelPosition: layout.panelPosition as "bottom" | "left" | "right",
                    }),
                  ...(typeof layout.panelAlign === "string" &&
                    ["left", "center", "justify", "right"].includes(layout.panelAlign) && {
                      panelAlign: layout.panelAlign as "left" | "center" | "justify" | "right",
                    }),
                  ...(typeof layout.showPanelIcons === "boolean" && {
                    showPanelIcons: layout.showPanelIcons,
                  }),
                  ...(typeof layout.bottomMaximized === "boolean" && {
                    bottomMaximized: layout.bottomMaximized,
                  }),
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
