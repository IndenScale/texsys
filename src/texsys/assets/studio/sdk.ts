import type { PreviewArtifact } from "./project-preview-model";
import type { ComponentType, ReactNode } from "react";
import type { MenuItem } from "./menus";

/** Supplied by the host, never inferred from the OS. Web uses external window controls. */
export type StudioChrome = {
  nativeMenu: boolean;
  windowControls: "external" | "left" | "right";
};

export type StudioLayout = {
  sidebarOpen: boolean;
  sidebarActivity?: string;
  sidebarPosition?: "left" | "right";
  explorerOpenEditors?: boolean;
  activityBarPosition?: "side" | "top" | "bottom";
  activityBarSize?: "default" | "compact";
  hiddenActivities?: string[];
  hiddenActivityBadges?: string[];
  inspectorOpen: boolean;
  bottomOpen: boolean;
  bottomMaximized?: boolean;
  bottomTab: "problems" | "tasks" | "terminal" | string;
  sidebarWidth: number;
  inspectorWidth: number;
  bottomHeight: number;
  hiddenBottomTabs?: string[];
  hideBadges?: Record<string, boolean>;
  panelPosition?: "bottom" | "left" | "right";
  panelWidth?: number;
  panelAlign?: "left" | "center" | "justify" | "right";
  showPanelIcons?: boolean;
};

export type PluginManifest = {
  id: string;
  name: string;
  discipline: string;
  version: string;
  api_version: number;
  capabilities: string[];
};
export type StudioJob = {
  id: string;
  plugin: string;
  action: string;
  state: string;
  revision: string;
  current: boolean;
  created_at: string;
  finished_at?: string;
  log: string;
  message?: string;
  result?: { status: string; message?: string; diagnostics?: Record<string, unknown>[] };
  options: Record<string, unknown>;
};
export type StudioState = {
  api_version: number;
  project: {
    name: string;
    path: string;
    revision: string;
    branch?: { kind: "branch" | "detached" | "unversioned" | "unavailable"; name?: string };
  };
  plugins: PluginManifest[];
  data: Record<string, unknown>;
  jobs: StudioJob[];
  diagnostics: { plugin?: string; message: string }[];
};
export type ProjectFile = {
  path: string;
  name: string;
  parent: string;
  size: number;
  generated?: boolean;
  preview?: FilePreviewFormat;
  artifact?: PreviewArtifact;
  preview_error?: string;
};
export type FilePreview = {
  path: string;
  size: number;
  previewable: boolean;
  content?: string;
  reason?: string;
  version?: string;
  generated?: boolean;
  preview?: FilePreviewFormat;
  artifact?: PreviewArtifact;
  preview_error?: string;
};
export type FilePreviewFormat = {
  kind: "diagram" | "schematic" | "topology" | "scene";
  source_map?: Record<string, import("./project-preview-model").PreviewSource>;
};
export type WorkbenchRunCommand = MenuItem & {
  disabled?: boolean;
  run: () => void;
};
export type WorkbenchProps = {
  state: StudioState;
  data: unknown;
  refresh: () => Promise<void>;
  api: <T>(path: string, body?: unknown) => Promise<T>;
  notify: (message: string, error?: boolean) => void;
  showTasks: (jobId?: string) => void;
  taskResult?: { job: StudioJob; token: number };
  layout: StudioLayout;
  changeLayout: (patch: Partial<StudioLayout>) => void;
  taskPanel: ReactNode;
  /** Publish commands for this discipline's Run menu and the command palette. */
  registerRunCommands?: (plugin: string, commands: readonly WorkbenchRunCommand[]) => () => void;
  navigationRequest?: { command: string; token: number; path?: string };
  onNavigationState?: (state: {
    canBack: boolean;
    canForward: boolean;
    canClose?: boolean;
    canNavigateProblems?: boolean;
    /** Count of current items shown in the Problems panel. */
    problemsCount?: number;
    objectDefinition?: string;
  }) => void;
};
export type WorkbenchObject = MenuItem & {
  kind: "definition" | "instance";
  definition: string;
};
export type WorkbenchObjectIndex = { objects: WorkbenchObject[]; truncated: boolean };

export type WorkbenchPlugin = {
  manifest: PluginManifest;
  Workbench: ComponentType<WorkbenchProps>;
  views?: readonly MenuItem[];
  objectIndex?: (data: unknown) => WorkbenchObjectIndex | undefined;
};

export const labels: Record<string, string> = {
  pass: "通过",
  fail: "未满足",
  error: "执行错误",
  indeterminate: "待确认",
  running: "运行中",
  completed: "执行完成",
  failed: "执行失败",
  cancelled: "已取消",
  interrupted: "已中断",
  check: "模型检查",
  build: "构建图纸",
  simulate: "动态仿真",
  known: "已知",
  assumed: "假设",
  unknown: "待定",
  missing: "缺失",
  "not-applicable": "不适用",
};
