import type { ComponentType, ReactNode } from "react";

/** Supplied by the host, never inferred from the OS. Web uses external window controls. */
export type StudioChrome = {
  nativeMenu: boolean;
  windowControls: "external" | "left" | "right";
};

export type StudioLayout = {
  sidebarOpen: boolean;
  inspectorOpen: boolean;
  bottomOpen: boolean;
  bottomTab: "problems" | "tasks";
  sidebarWidth: number;
  inspectorWidth: number;
  bottomHeight: number;
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
export type ProjectFile = { path: string; name: string; parent: string; size: number };
export type FilePreview = {
  path: string;
  size: number;
  previewable: boolean;
  content?: string;
  reason?: string;
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
  navigationRequest?: { command: string; token: number; path?: string };
  onNavigationState?: (state: { canBack: boolean; canForward: boolean }) => void;
};
export type WorkbenchPlugin = {
  manifest: PluginManifest;
  Workbench: ComponentType<WorkbenchProps>;
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
