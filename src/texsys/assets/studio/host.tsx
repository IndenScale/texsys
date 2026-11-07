import React, { Component, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon, Empty } from "./ui";
import {
  labels,
  type StudioJob,
  type StudioState,
  type WorkbenchPlugin,
  type StudioLayout,
  type ProjectFile,
  type StudioChrome,
} from "./sdk";
import { CoreWorkspaceProvider, WorkbenchSurface } from "./CoreWorkspace";
import { DEFAULT_SIDEBAR_WIDTH } from "./atomstudio/layoutSizing";
import {
  readLayoutPreferences,
  writeLayoutPreferences,
  type LayoutPreferences,
} from "./layout-preferences";
import { CommandCenter } from "./CommandCenter";
import { AnimatedPresence } from "./AnimatedPresence";
import { ToolbarButton } from "./ToolbarButton";
import { ApplicationMenuBar } from "./ApplicationMenuBar";
import type { SearchScope } from "./search";
import { studioMenus } from "./menus";
import { t, useLocale, getLocale, localizeError } from "./i18n";
import "./studio.css";

const token =
  document.querySelector<HTMLMetaElement>('meta[name="tex-studio-token"]')?.content ?? "";
async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "X-Tex-Studio-Token": token,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(localizeError(data.error ?? `Request failed: ${response.status}`));
  return data as T;
}

class WorkbenchBoundary extends Component<
  { children: React.ReactNode; fallback?: React.ReactNode },
  { error: string }
> {
  state = { error: "" };
  static getDerivedStateFromError(error: Error) {
    return { error: error.message };
  }
  render() {
    return this.state.error
      ? (this.props.fallback ?? (
          <Empty title={t("工作台暂时无法显示")}>
            {this.state.error}
            {t("。请刷新项目，检查输入和插件诊断。")}
          </Empty>
        ))
      : this.props.children;
  }
}

function TaskPanel({
  state,
  refresh,
  notify,
  initialJob,
  openResult,
}: {
  state: StudioState;
  refresh: () => Promise<void>;
  notify: (message: string, error?: boolean) => void;
  initialJob?: string;
  openResult: (job: StudioJob) => void;
}) {
  const [selected, setSelected] = useState<string | undefined>(initialJob);
  const [log, setLog] = useState("");
  const job = state.jobs.find((item) => item.id === selected) ?? state.jobs[0];
  useEffect(() => setLog(""), [job?.id]);
  useEffect(() => {
    if (initialJob) setSelected(initialJob);
  }, [initialJob]);
  useEffect(() => {
    if (!job) return;
    let valid = true;
    fetch(`/api/artifact?path=${encodeURIComponent(job.log)}`, {
      headers: { "X-Tex-Studio-Token": token },
    })
      .then((response) => response.text())
      .then((value) => {
        if (valid) setLog(value);
      })
      .catch(() => {
        if (valid) setLog(t("日志暂时不可读。"));
      });
    return () => {
      valid = false;
    };
  }, [job?.id, job?.state, state.jobs]);
  return (
    <section className="task-panel" aria-label={t("后台任务")}>
      <div className="task-drawer-body">
        <div className="task-list">
          {state.jobs.length ? (
            state.jobs.map((item) => (
              <button
                key={item.id}
                className={item.id === job?.id ? "selected" : ""}
                onClick={() => setSelected(item.id)}
              >
                <span className={`status-dot ${item.state}`} />
                <span>
                  {t(labels[item.action]) ?? item.action}
                  <small>
                    {new Date(item.created_at).toLocaleTimeString(getLocale())} ·{" "}
                    {item.id.slice(0, 8)}
                  </small>
                </span>
                <em>{t(labels[item.state]) ?? item.state}</em>
              </button>
            ))
          ) : (
            <p className="muted">{t("尚未提交任务。已启用的扩展可在这里显示运行记录。")}</p>
          )}
        </div>
        {job && (
          <div className="task-detail">
            <div className="task-summary">
              <span className={`tag ${job.result?.status ?? job.state}`}>
                {t(labels[job.state]) ?? job.state}
              </span>
              {job.result && (
                <span>
                  {t("工程结论：")}
                  <strong>{t(labels[job.result.status]) ?? job.result.status}</strong>
                </span>
              )}
              <span className={job.current ? "muted" : "warning-text"}>
                {job.current ? t("输入与当前项目一致") : t("历史输入版本")}
              </span>
              {job.state === "completed" && (
                <button className="small-button" onClick={() => openResult(job)}>
                  {job.action === "simulate"
                    ? t("打开工况结果")
                    : job.action === "check"
                      ? t("打开检查问题")
                      : t("打开图纸成果")}
                  <Icon name="arrow" size={14} />
                </button>
              )}
              {job.state === "running" && (
                <button
                  className="small-button"
                  onClick={async () => {
                    try {
                      await api(`/api/jobs/${job.id}/cancel`, {});
                      await refresh();
                      notify(t("任务已取消"));
                    } catch (error) {
                      notify(String(error), true);
                    }
                  }}
                >
                  {t("取消任务")}
                </button>
              )}
            </div>
            {(job.message || job.result?.message) && (
              <p className="error-text">{job.message ?? job.result?.message}</p>
            )}
            <pre>{log || t("正在准备执行日志…")}</pre>
          </div>
        )}
      </div>
    </section>
  );
}

function initialLayout(): StudioLayout {
  return {
    sidebarOpen: window.innerWidth > 760,
    inspectorOpen: false,
    bottomOpen: false,
    bottomTab: "problems",
    sidebarWidth: DEFAULT_SIDEBAR_WIDTH,
    inspectorWidth: 320,
    bottomHeight: 260,
  };
}

const macKeys = /Mac|iPhone|iPad/.test(navigator.platform);

function Studio({ plugins, chrome }: { plugins: WorkbenchPlugin[]; chrome: StudioChrome }) {
  const locale = useLocale();
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);
  const [fileRequest, setFileRequest] = useState<{ path: string; token: number }>();
  const [settingsRequest, setSettingsRequest] = useState(0);
  const [activityRequest, setActivityRequest] = useState<{
    id: "explorer" | "search" | "scm";
    token: number;
  }>();
  const [state, setState] = useState<StudioState>();
  const [commandOpen, setCommandOpen] = useState(false);
  const [commandScope, setCommandScope] = useState<SearchScope>("all");
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(false),
    [filesError, setFilesError] = useState("");
  const [filesTruncated, setFilesTruncated] = useState(false);
  const [commandFocusRequest, setCommandFocusRequest] = useState(0);
  const commandOrigin = useRef<HTMLElement | null>(null);
  const closeCommands = useCallback(() => setCommandOpen(false), []);
  const openCommands = (scope: SearchScope = "all") => {
    if (!commandOpen) commandOrigin.current = document.activeElement as HTMLElement | null;
    setMenu(null);
    setCommandScope(scope);
    setCommandOpen(true);
    setCommandFocusRequest((previous) => previous + 1);
  };
  const [coreHistory, setCoreHistory] = useState({
    active: true,
    canBack: false,
    canForward: false,
  });
  const reportCoreHistory = useCallback(
    (next: typeof coreHistory) =>
      setCoreHistory((previous) =>
        previous.active === next.active &&
        previous.canBack === next.canBack &&
        previous.canForward === next.canForward
          ? previous
          : next,
      ),
    [],
  );
  const [historyStates, setHistoryStates] = useState<
    Record<string, { canBack: boolean; canForward: boolean }>
  >({});
  const [menu, setMenu] = useState<"project" | "discipline" | null>(null);
  const [defaultLayout] = useState(initialLayout);
  const [workbench, setWorkbench] = useState<LayoutPreferences & { project: string }>({
    project: "",
    discipline: "",
    layouts: {},
  });
  const { discipline: active, layouts, project } = workbench;
  const setActive = (discipline: string) =>
    setWorkbench((previous) => ({ ...previous, discipline }));
  const [taskId, setTaskId] = useState<string>();
  const [taskResult, setTaskResult] = useState<{ job: StudioJob; token: number }>();
  const [navigation, setNavigation] = useState<
    Record<string, { command: string; token: number; path?: string }>
  >({});
  const [error, setError] = useState("");
  const [toast, setToast] = useState<{ message: string; error?: boolean }>();
  const mounted = useRef(true),
    refreshing = useRef(false);
  const notify = useCallback(
    (message: string, isError = false) => setToast({ message, error: isError }),
    [],
  );
  const refresh = useCallback(async () => {
    if (refreshing.current) return;
    refreshing.current = true;
    try {
      const next = await api<StudioState>("/api/state");
      if (mounted.current) {
        setState(next);
        setWorkbench((previous) => {
          const project = next.project.path;
          const preferences =
            previous.project === project
              ? previous
              : { project, ...readLayoutPreferences(project, defaultLayout) };
          const discipline = next.plugins.some(({ id }) => id === preferences.discipline)
            ? preferences.discipline
            : (next.plugins[0]?.id ?? "");
          return discipline === preferences.discipline
            ? preferences
            : { ...preferences, discipline };
        });
        setError("");
      }
    } catch (error) {
      if (mounted.current) setError(error instanceof Error ? error.message : String(error));
    } finally {
      refreshing.current = false;
    }
  }, [defaultLayout]);
  useEffect(() => {
    if (project) writeLayoutPreferences(project, { discipline: active, layouts });
  }, [project, active, layouts]);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    const timer = setInterval(() => void refresh(), 2500);
    return () => {
      mounted.current = false;
      clearInterval(timer);
    };
  }, [refresh]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(undefined), 6500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
  const updateLayout = (id: string, patch: Partial<StudioLayout>) =>
    setWorkbench((previous) => ({
      ...previous,
      layouts: {
        ...previous.layouts,
        [id]: {
          ...(Object.hasOwn(previous.layouts, id) ? previous.layouts[id] : defaultLayout),
          ...patch,
        },
      },
    }));
  const layout = layouts[active] ?? defaultLayout;
  const available = plugins.filter((plugin) =>
    state?.plugins.some((item) => item.id === plugin.manifest.id),
  );
  const current = available.find((plugin) => plugin.manifest.id === active);
  const running = state?.jobs.filter((job) => job.state === "running").length ?? 0;
  const showTasks = (id?: string) => {
    setTaskId(id);
    updateLayout(active, { bottomOpen: true, bottomTab: "tasks" });
  };
  const requestNavigation = (command: string) => {
    setNavigation((previous) => ({
      ...previous,
      [active]: { command, token: (previous[active]?.token ?? 0) + 1 },
    }));
    setMenu(null);
  };
  const historyState = coreHistory.active
    ? coreHistory
    : (historyStates[active] ?? { canBack: false, canForward: false });
  const invokeCommand = (id: string) => {
    setMenu(null);
    switch (id) {
      case "commands.open":
        openCommands("commands");
        break;
      case "workspace.explorer":
      case "workspace.search":
      case "workspace.scm":
        setActivityRequest((previous) => ({
          id: id.split(".")[1] as "explorer" | "search" | "scm",
          token: (previous?.token ?? 0) + 1,
        }));
        break;
      case "files.search":
        openCommands("files");
        break;
      case "project.overview":
        if (!current)
          setActivityRequest((previous) => ({ id: "explorer", token: (previous?.token ?? 0) + 1 }));
        else requestNavigation("overview");
        break;
      case "project.refresh":
        void refresh();
        break;
      case "view.sidebar":
        updateLayout(active, { sidebarOpen: !layout.sidebarOpen });
        break;
      case "view.bottom":
        updateLayout(active, { bottomOpen: !layout.bottomOpen });
        break;
      case "view.inspector":
        updateLayout(active, { inspectorOpen: !layout.inspectorOpen });
        break;
      case "navigation.back":
        if (historyState.canBack) requestNavigation(id);
        break;
      case "navigation.forward":
        if (historyState.canForward) requestNavigation(id);
        break;
      case "tasks.show":
        showTasks();
        break;
      case "problems.show":
        updateLayout(active, { bottomOpen: true, bottomTab: "problems" });
        break;
      case "help.capabilities":
        requestNavigation("capabilities");
        break;
    }
  };
  const localizedMenus = useMemo(
    () =>
      studioMenus.map((group) => ({
        ...group,
        label: t(group.label),
        items: group.items.map((item) => ({ ...item, label: t(item.label) })),
      })),
    [locale],
  );
  const menuItems = localizedMenus.flatMap((group) =>
    group.items.map((item) => ({
      ...item,
      group: group.label,
      keys:
        item.id === "commands.open"
          ? macKeys
            ? ["⇧", "⌘", "P"]
            : ["Ctrl", "Shift", "P"]
          : item.id === "navigation.back"
            ? macKeys
              ? ["⌘", "["]
              : ["Alt", "←"]
            : item.id === "navigation.forward"
              ? macKeys
                ? ["⌘", "]"]
                : ["Alt", "→"]
              : item.id === "view.sidebar"
                ? [macKeys ? "⌘" : "Ctrl", "\\"]
                : item.id === "files.search"
                  ? [macKeys ? "⌘" : "Ctrl", "P"]
                  : undefined,
      disabled:
        !state ||
        (item.id === "navigation.back" && !historyState.canBack) ||
        (item.id === "navigation.forward" && !historyState.canForward) ||
        (item.id === "help.capabilities" && !current),
      checked:
        item.id === "view.sidebar"
          ? layout.sidebarOpen
          : item.id === "view.bottom"
            ? layout.bottomOpen
            : item.id === "view.inspector"
              ? layout.inspectorOpen
              : undefined,
    })),
  );
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.shiftKey && event.key.toLowerCase() === "p") {
        event.preventDefault();
        openCommands("commands");
        return;
      }
      if ((event.metaKey || event.ctrlKey) && !event.shiftKey && event.key.toLowerCase() === "p") {
        event.preventDefault();
        openCommands("files");
        return;
      }
      if (
        commandOpen ||
        (event.target as HTMLElement).matches("input,textarea,select,[contenteditable=true]")
      )
        return;
      if (
        (event.altKey && event.key === "ArrowLeft") ||
        ((event.metaKey || event.ctrlKey) && event.key === "[")
      ) {
        event.preventDefault();
        invokeCommand("navigation.back");
      }
      if (
        (event.altKey && event.key === "ArrowRight") ||
        ((event.metaKey || event.ctrlKey) && event.key === "]")
      ) {
        event.preventDefault();
        invokeCommand("navigation.forward");
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [active, historyState.canBack, historyState.canForward, commandOpen]);
  useEffect(() => {
    if (!commandOpen || commandScope === "commands") return;
    let current = true;
    setFilesLoading(true);
    setFilesError("");
    void api<{ files: ProjectFile[]; truncated: boolean }>("/api/files")
      .then((data) => {
        if (current) {
          setFiles(data.files);
          setFilesTruncated(data.truncated);
        }
      })
      .catch((error) => {
        if (current) setFilesError(String(error));
      })
      .finally(() => {
        if (current) setFilesLoading(false);
      });
    return () => {
      current = false;
    };
  }, [commandOpen, commandFocusRequest, commandScope]);
  const openFile = (file: ProjectFile) => {
    setFileRequest((previous) => ({ path: file.path, token: (previous?.token ?? 0) + 1 }));
  };
  const branch = state?.project.branch;
  const branchLabel =
    branch?.kind === "branch"
      ? branch.name
      : branch?.kind === "detached"
        ? t("游离 {0}", { "0": branch.name })
        : branch?.kind === "unversioned"
          ? t("无版本库")
          : t("分支不可用");
  return (
    <div className="studio-app">
      <header
        className={`studio-titlebar ${chrome.nativeMenu ? "has-native-menu" : "has-app-menu"} window-controls-${chrome.windowControls}`}
        aria-label={t("窗口标题栏")}
      >
        <div className="titlebar-leading">
          {chrome.windowControls === "left" && (
            <div className="window-controls-reserve" aria-hidden="true" />
          )}
          {!chrome.nativeMenu && (
            <ApplicationMenuBar
              groups={localizedMenus}
              commands={menuItems}
              invoke={invokeCommand}
              onOpen={() => {
                setMenu(null);
                closeCommands();
              }}
              dismiss={commandOpen || menu !== null}
            />
          )}
          <div className="titlebar-controls" role="toolbar" aria-label={t("导航与布局")}>
            <div className="titlebar-control-group" role="group" aria-label={t("文档导航")}>
              <ToolbarButton
                aria-label={t("后退")}
                tooltip={t("后退")}
                shortcut={macKeys ? ["⌘", "["] : ["Alt", "←"]}
                disabled={!historyState.canBack}
                onClick={() => requestNavigation("navigation.back")}
              >
                <Icon name="back" size={18} />
              </ToolbarButton>
              <ToolbarButton
                aria-label={t("前进")}
                tooltip={t("前进")}
                shortcut={macKeys ? ["⌘", "]"] : ["Alt", "→"]}
                disabled={!historyState.canForward}
                onClick={() => requestNavigation("navigation.forward")}
              >
                <Icon name="forward" size={18} />
              </ToolbarButton>
            </div>
            <div className="titlebar-control-group" role="group" aria-label={t("面板布局")}>
              <ToolbarButton
                aria-label={t("切换资源面板")}
                tooltip={t("显示/隐藏资源面板")}
                shortcut={[macKeys ? "⌘" : "Ctrl", "\\"]}
                aria-pressed={layout.sidebarOpen}
                onClick={() => updateLayout(active, { sidebarOpen: !layout.sidebarOpen })}
              >
                <Icon name="sidebar" size={18} expanded={layout.sidebarOpen} />
              </ToolbarButton>
              <ToolbarButton
                aria-label={t("切换底部面板")}
                tooltip={t("显示/隐藏底部面板")}
                aria-pressed={layout.bottomOpen}
                onClick={() => updateLayout(active, { bottomOpen: !layout.bottomOpen })}
              >
                <Icon name="bottom-panel" size={18} expanded={layout.bottomOpen} />
              </ToolbarButton>
              <ToolbarButton
                aria-label={t("切换属性面板")}
                tooltip={t("显示/隐藏属性面板")}
                aria-pressed={layout.inspectorOpen}
                onClick={() => updateLayout(active, { inspectorOpen: !layout.inspectorOpen })}
              >
                <Icon name="inspector" size={18} expanded={layout.inspectorOpen} />
              </ToolbarButton>
            </div>
          </div>
        </div>
        <CommandCenter
          project={state?.project.name ?? t("打开项目中")}
          disabled={!state}
          open={commandOpen}
          focusRequest={commandFocusRequest}
          returnFocus={commandOrigin}
          commands={menuItems.filter((item) => item.id !== "commands.open")}
          onOpen={() => openCommands("all")}
          onClose={closeCommands}
          invoke={invokeCommand}
          shortcut={[macKeys ? "⌘" : "Ctrl", "P"]}
          scope={commandScope}
          onScopeChange={setCommandScope}
          files={files}
          filesLoading={filesLoading}
          filesError={filesError}
          filesTruncated={filesTruncated}
          onFile={openFile}
        />
        <div className="titlebar-trailing">
          <ToolbarButton
            className="settings-trigger"
            aria-label={t("设置")}
            tooltip={t("设置")}
            disabled={!state}
            onClick={() => {
              setMenu(null);
              closeCommands();
              setSettingsRequest((request) => request + 1);
            }}
          >
            <Icon name="settings" size={18} />
          </ToolbarButton>
          {chrome.windowControls === "right" && (
            <div className="window-caption-reserve" aria-hidden="true" />
          )}
        </div>
      </header>
      <main className="studio-workspace">
        {error && (
          <div className="connection-error" role="alert">
            {t("项目连接异常：")}
            {error}
            <button onClick={() => void refresh()}>{t("重试")}</button>
          </div>
        )}
        {!state ? (
          <Empty title={t("正在打开工作空间")}>{t("读取项目、专业和运行记录。")}</Empty>
        ) : (
          <CoreWorkspaceProvider
            state={state}
            api={api}
            refresh={refresh}
            notify={notify}
            changeLayout={(patch) => updateLayout(active, patch)}
            sidebarActivity={layout.sidebarActivity}
            fileRequest={fileRequest}
            activityRequest={activityRequest}
            settingsRequest={settingsRequest}
            navigationRequest={navigation[active]}
            onNavigationState={reportCoreHistory}
            hasDomain={available.length > 0}
          >
            {available.length === 0 ? (
              <WorkbenchSurface
                layout={layout}
                change={(patch) => updateLayout(active, patch)}
                bottom={
                  <TaskPanel
                    state={state}
                    refresh={refresh}
                    notify={notify}
                    openResult={() => {}}
                  />
                }
              />
            ) : (
              available.map((plugin) => {
                const Workbench = plugin.Workbench,
                  id = plugin.manifest.id;
                return (
                  <div key={id} hidden={id !== active} className="professional-workbench">
                    <WorkbenchBoundary
                      fallback={
                        <WorkbenchSurface
                          layout={layouts[id] ?? defaultLayout}
                          change={(patch) => updateLayout(id, patch)}
                        >
                          <Empty title={t("专业预览暂时无法显示")}>
                            {t("请检查项目输入和插件诊断。")}
                          </Empty>
                        </WorkbenchSurface>
                      }
                    >
                      <Workbench
                        state={state}
                        data={state.data[id]}
                        refresh={refresh}
                        api={api}
                        notify={notify}
                        layout={layouts[id] ?? defaultLayout}
                        changeLayout={(patch) => updateLayout(id, patch)}
                        showTasks={(jobId) => {
                          setTaskId(jobId);
                          updateLayout(id, { bottomOpen: true, bottomTab: "tasks" });
                        }}
                        taskResult={taskResult}
                        navigationRequest={navigation[id]}
                        onNavigationState={(next) =>
                          setHistoryStates((previous) =>
                            previous[id]?.canBack === next.canBack &&
                            previous[id]?.canForward === next.canForward
                              ? previous
                              : { ...previous, [id]: next },
                          )
                        }
                        taskPanel={
                          <TaskPanel
                            state={state}
                            refresh={refresh}
                            notify={notify}
                            initialJob={taskId}
                            openResult={(job) => {
                              setActive(job.plugin);
                              setTaskResult((previous) => ({
                                job,
                                token: (previous?.token ?? 0) + 1,
                              }));
                              updateLayout(job.plugin, { bottomOpen: false });
                            }}
                          />
                        }
                      />
                    </WorkbenchBoundary>
                  </div>
                );
              })
            )}
          </CoreWorkspaceProvider>
        )}
      </main>
      <footer className="studio-statusbar" aria-label={t("工作空间状态")}>
        <div className="project-context context-item">
          <button
            className="context-button"
            aria-haspopup="menu"
            aria-label={t("当前项目")}
            aria-expanded={menu === "project"}
            title={state?.project.path}
            onClick={() => setMenu(menu === "project" ? null : "project")}
          >
            <Icon name="folder" size={16} />
            <span>{state?.project.name ?? t("打开项目中")}</span>
            <Icon name="chevron" size={13} />
          </button>
          <AnimatedPresence show={menu === "project"}>
            <button
              className="menu-backdrop"
              aria-label={t("关闭项目菜单")}
              onClick={() => setMenu(null)}
            />
            <div className="context-menu" role="menu" aria-label={t("项目菜单")}>
              <p>{state?.project.name}</p>
              <small className="context-path">{state?.project.path}</small>
              <button role="menuitem" onClick={() => requestNavigation("overview")}>
                <Icon name="folder" size={16} />
                {t("项目概览")}
              </button>
              <button
                role="menuitem"
                onClick={() => {
                  void refresh();
                  setMenu(null);
                }}
              >
                <Icon name="refresh" size={16} />
                {t("读取外部修改")}
              </button>
              <button role="menuitem" onClick={() => requestNavigation("capabilities")}>
                <Icon name="info" size={16} />
                {t("专业能力与范围")}
              </button>
            </div>
          </AnimatedPresence>
        </div>
        <span className="context-separator">/</span>
        <div className="discipline-context context-item">
          <button
            className="context-button"
            aria-haspopup="menu"
            aria-label={t("当前专业")}
            aria-expanded={menu === "discipline"}
            onClick={() => setMenu(menu === "discipline" ? null : "discipline")}
          >
            <span>{t(current?.manifest.discipline) ?? t("通用编辑")}</span>
            <Icon name="chevron" size={13} />
          </button>
          <AnimatedPresence show={menu === "discipline"}>
            <button
              className="menu-backdrop"
              aria-label={t("关闭专业菜单")}
              onClick={() => setMenu(null)}
            />
            <div className="context-menu" role="menu" aria-label={t("专业菜单")}>
              <p>{t("专业工作台")}</p>
              {available.map((plugin) => (
                <button
                  key={plugin.manifest.id}
                  role="menuitem"
                  onClick={() => {
                    setActive(plugin.manifest.id);
                    setMenu(null);
                  }}
                >
                  <Icon name="grid" size={16} />
                  <span>{t(plugin.manifest.discipline)}</span>
                  {plugin.manifest.id === active && <Icon name="tick" size={14} />}
                </button>
              ))}
            </div>
          </AnimatedPresence>
        </div>
        <span className="context-separator">/</span>
        <div
          className="branch-context"
          aria-label={t("当前分支")}
          title={
            branch?.kind === "branch" ? t("项目 Git 分支：{0}", { "0": branch.name }) : branchLabel
          }
        >
          <Icon name="branch" size={15} />
          <span>{branchLabel}</span>
        </div>

        <span className="statusbar-spacer" />
        <span className="statusbar-connection" title={state?.project.path}>
          <i className={error ? "offline-dot" : "online-dot"} />
          {error ? t("连接中断") : t("本地")}
        </span>
        {current && <span className="statusbar-review">{t("概念设计 · 待工程审核")}</span>}
        <span className="statusbar-revision" title={state?.project.revision}>
          {state ? t("修订 {0}", { "0": state.project.revision.slice(7, 15) }) : t("读取中")}
        </span>
        <button className="statusbar-runs" onClick={() => showTasks()}>
          {running
            ? t("{0} 个任务运行中", { "0": running })
            : t("{0} 次运行", { "0": state?.jobs.length ?? 0 })}
        </button>
      </footer>
      <AnimatedPresence show={!!toast}>
        <div className={`studio-toast ${toast?.error ? "error" : ""}`} role="status">
          <Icon name={toast?.error ? "info" : "check"} />
          {toast?.message}
          <button
            className="icon-button"
            aria-label={t("关闭提示")}
            onClick={() => setToast(undefined)}
          >
            <Icon name="close" size={14} />
          </button>
        </div>
      </AnimatedPresence>
    </div>
  );
}

export function startStudio(
  plugins: WorkbenchPlugin[],
  chrome: StudioChrome = { nativeMenu: false, windowControls: "external" },
) {
  const seen = new Set<string>();
  for (const plugin of plugins) {
    if (plugin.manifest.api_version !== 1 || seen.has(plugin.manifest.id))
      throw new Error("Incompatible or duplicate Studio frontend contribution.");
    seen.add(plugin.manifest.id);
  }
  createRoot(document.getElementById("root")!).render(<Studio plugins={plugins} chrome={chrome} />);
}
