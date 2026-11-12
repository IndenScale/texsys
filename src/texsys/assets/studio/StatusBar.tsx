import React, { useRef, useState, useSyncExternalStore } from "react";
import type { SourceEditorStatus } from "./editor-groups";
import { t } from "./i18n";
import { NavigatorMenu, type NavigatorMenuItem } from "./NavigatorMenu";
import { Icon } from "./ui";
import "./statusbar.css";

const items = [
  { id: "connection", label: "连接状态" },
  { id: "project", label: "当前项目" },
  { id: "branch", label: "当前分支" },
  { id: "discipline", label: "当前专业" },
  { id: "problems", label: "问题" },
  { id: "tasks", label: "任务与日志" },
  { id: "position", label: "光标位置" },
  { id: "indent", label: "编辑器缩进" },
  { id: "encoding", label: "编辑器编码" },
  { id: "language", label: "编辑器语言" },
] as const;
type ItemId = (typeof items)[number]["id"];
type StatusBarPreferences = { visible: boolean; hidden: ItemId[] };
const preferencesKey = "texsys.studio.statusbar";

function readPreferences(): StatusBarPreferences {
  try {
    const saved = JSON.parse(localStorage.getItem(preferencesKey) ?? "null");
    return {
      visible: saved?.visible !== false,
      hidden: Array.isArray(saved?.hidden)
        ? items.map(({ id }) => id).filter((id) => saved.hidden.includes(id))
        : [],
    };
  } catch {
    return { visible: true, hidden: [] };
  }
}

export function useStatusBarPreferences() {
  const [preferences, setPreferences] = useState(readPreferences);
  const update = (next: StatusBarPreferences) => {
    setPreferences(next);
    try {
      localStorage.setItem(preferencesKey, JSON.stringify(next));
    } catch {
      // Keep the current page usable without browser storage.
    }
  };
  return {
    ...preferences,
    setVisible: (visible: boolean) => update({ ...preferences, visible }),
    toggleItem: (id: ItemId) =>
      update({
        ...preferences,
        hidden: preferences.hidden.includes(id)
          ? preferences.hidden.filter((item) => item !== id)
          : [...preferences.hidden, id],
      }),
  };
}

export function createSourceStatusStore() {
  let current: SourceEditorStatus | undefined;
  const listeners = new Set<() => void>();
  return {
    get: () => current,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    set: (next?: SourceEditorStatus) => {
      if (
        current === next ||
        (current &&
          next &&
          current.line === next.line &&
          current.column === next.column &&
          current.tabSize === next.tabSize &&
          current.insertSpaces === next.insertSpaces &&
          current.language === next.language)
      )
        return;
      current = next;
      listeners.forEach((listener) => listener());
    },
  };
}

const languages: Record<string, string> = {
  plaintext: "纯文本",
  json: "JSON",
  markdown: "Markdown",
  ini: "INI",
  toml: "TOML",
  python: "Python",
  shell: "Shell",
  yaml: "YAML",
};

export function StatusBar({
  preferences,
  project,
  projectActions,
  branch,
  discipline,
  disciplineActions,
  connection,
  problems,
  jobs,
  running,
  sourceStore,
  onBranch,
  onProblems,
  onTasks,
  onGoToLine,
  onHide,
}: {
  preferences: ReturnType<typeof useStatusBarPreferences>;
  project?: { name: string; path: string; revision: string };
  projectActions: NavigatorMenuItem[];
  branch?: { label: string; description: string; canOpen: boolean };
  discipline?: string;
  disciplineActions: NavigatorMenuItem[];
  connection: "loading" | "online" | "offline";
  problems?: number;
  jobs: number;
  running: number;
  sourceStore: ReturnType<typeof createSourceStatusStore>;
  onBranch: () => void;
  onProblems: () => void;
  onTasks: () => void;
  onGoToLine: () => void;
  onHide: () => void;
}) {
  const source = useSyncExternalStore(sourceStore.subscribe, sourceStore.get);
  const bar = useRef<HTMLElement>(null);
  const origin = useRef<HTMLElement>(null);
  const [point, setPoint] = useState<{ x: number; y: number }>();
  const openMenu = (x: number, target: EventTarget) => {
    origin.current = target instanceof Element ? target.closest("button") : null;
    setPoint({ x, y: bar.current!.getBoundingClientRect().top });
  };
  const show = (id: ItemId) => !preferences.hidden.includes(id);
  const connectionLabel =
    connection === "offline" ? t("连接中断") : connection === "online" ? t("本地") : t("读取中");
  const problemLabel = problems === undefined ? t("问题") : t("当前问题：{0}", { "0": problems });
  const taskLabel = running
    ? t("{0} 个任务运行中", { "0": running })
    : t("任务与日志：{0} 次运行", { "0": jobs });
  const branchContent = branch && (
    <>
      <Icon name="branch" size={14} />
      <span className="statusbar-label">{branch.label}</span>
    </>
  );

  return (
    <footer
      ref={bar}
      className="studio-statusbar"
      aria-label={t("工作空间状态")}
      aria-keyshortcuts="Shift+F10"
      tabIndex={0}
      onContextMenu={(event) => {
        event.preventDefault();
        openMenu(event.clientX, event.target);
      }}
      onKeyDown={(event) => {
        if (event.defaultPrevented || (event.target as HTMLElement).closest('[role="menu"]'))
          return;
        if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
        event.preventDefault();
        openMenu((event.target as HTMLElement).getBoundingClientRect().left, event.target);
      }}
    >
      <div className="statusbar-group statusbar-workspace">
        {show("connection") && (
          <span
            className="statusbar-item statusbar-connection"
            data-state={connection}
            title={connectionLabel}
            aria-label={connectionLabel}
            role="status"
          >
            <i aria-hidden="true" />
            <span className="statusbar-label">{connectionLabel}</span>
          </span>
        )}
        {show("project") && project && (
          <NavigatorMenu
            label={t("当前项目")}
            title={project.path}
            placement="start"
            direction="up"
            triggerClassName="statusbar-item statusbar-project"
            triggerIcon={
              <>
                <Icon name="folder" size={14} />
                <span className="statusbar-label">{project.name}</span>
                <Icon name="chevron" size={12} />
              </>
            }
            detail={
              <>
                <strong>{project.name}</strong>
                <span>{project.path}</span>
                <span>{t("输入修订：{0}", { "0": project.revision })}</span>
              </>
            }
            items={projectActions}
          />
        )}
        {show("branch") &&
          branch &&
          (branch.canOpen ? (
            <button
              type="button"
              className="statusbar-item statusbar-branch"
              title={branch.description}
              aria-label={t("打开源代码管理：{0}", { "0": branch.label })}
              onClick={onBranch}
            >
              {branchContent}
            </button>
          ) : (
            <span className="statusbar-item statusbar-branch" title={branch.description}>
              {branchContent}
            </span>
          ))}
        {show("discipline") && discipline && (
          <NavigatorMenu
            label={t("当前专业")}
            title={discipline}
            placement="start"
            direction="up"
            triggerClassName="statusbar-item statusbar-discipline"
            triggerIcon={
              <>
                <Icon name="grid" size={14} />
                <span className="statusbar-label">{discipline}</span>
                <Icon name="chevron" size={12} />
              </>
            }
            items={disciplineActions}
          />
        )}
        {show("problems") && project && (
          <button
            type="button"
            className="statusbar-item statusbar-problems"
            title={problemLabel}
            aria-label={problemLabel}
            onClick={onProblems}
          >
            <Icon name="warning" size={14} />
            {problems}
          </button>
        )}
        {show("tasks") && project && (
          <button
            type="button"
            className="statusbar-item statusbar-tasks"
            title={taskLabel}
            aria-label={taskLabel}
            data-running={running > 0}
            onClick={onTasks}
          >
            <Icon name="clock" size={14} />
            {running || jobs}
          </button>
        )}
      </div>
      {source && (
        <div
          className="statusbar-group statusbar-editor"
          aria-label={t("源码编辑器状态")}
          role="group"
        >
          {show("position") && (
            <button
              type="button"
              className="statusbar-item statusbar-position"
              title={t("转到行／列…")}
              onClick={onGoToLine}
            >
              {t("行 {0}，列 {1}", { "0": source.line, "1": source.column })}
            </button>
          )}
          {show("indent") && (
            <span className="statusbar-item statusbar-indent" title={t("编辑器缩进")}>
              {t(source.insertSpaces ? "空格：{0}" : "制表符：{0}", { "0": source.tabSize })}
            </span>
          )}
          {show("encoding") && (
            <span className="statusbar-item statusbar-encoding" title={t("源码文本编码")}>
              UTF-8
            </span>
          )}
          {show("language") && (
            <span className="statusbar-item statusbar-language" title={t("编辑器语言")}>
              {t(languages[source.language] ?? source.language)}
            </span>
          )}
        </div>
      )}
      {point && (
        <NavigatorMenu
          label={t("配置状态栏")}
          point={point}
          direction="up"
          onDismiss={() => {
            setPoint(undefined);
            if (document.activeElement !== document.body) return;
            if (origin.current?.isConnected) origin.current.focus();
            else bar.current?.focus();
          }}
          items={[
            { label: t("隐藏状态栏"), run: onHide },
            { label: "", separator: true },
            ...items.map(({ id, label }) => ({
              label: t(label),
              checkbox: true,
              checked: show(id),
              run: () => preferences.toggleItem(id),
            })),
          ]}
        />
      )}
    </footer>
  );
}
