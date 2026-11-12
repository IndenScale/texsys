import React, { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { sourceLanguages, type SourceEditorStatus, type SourceStatusAction } from "./editor-groups";
import type { StudioAiSettings } from "./ai-settings";
import { t } from "./i18n";
import { NavigatorMenu, type NavigatorMenuItem } from "./NavigatorMenu";
import { Icon } from "./ui";
import "./statusbar.css";

const standardItems = [
  { id: "connection", label: "远程主机" },
  { id: "branch", label: "当前分支" },
  { id: "problems", label: "问题" },
  { id: "screenReader", label: "屏幕阅读器模式" },
  { id: "position", label: "编辑器选择" },
  { id: "indent", label: "编辑器缩进" },
  { id: "encoding", label: "编辑器编码" },
  { id: "lineEnding", label: "编辑器结束行" },
  { id: "languageStatus", label: "编辑器语言状态" },
  { id: "language", label: "编辑器语言" },
  { id: "ai", label: "AI 状态" },
  { id: "keyboard", label: "键盘布局" },
  { id: "notifications", label: "通知" },
] as const;
const extensionItems = [
  { id: "project", label: "当前项目" },
  { id: "discipline", label: "当前专业" },
  { id: "engineeringProblems", label: "工程问题" },
  { id: "tasks", label: "任务与日志" },
] as const;
const items = [...standardItems, ...extensionItems];
type ItemId = (typeof items)[number]["id"];
export type StudioNotice = { id: number; message: string; error: boolean; time: number };
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
          current.language === next.language &&
          current.languageOverride === next.languageOverride &&
          current.selected === next.selected &&
          current.selections === next.selections &&
          current.format?.lineEnding === next.format?.lineEnding &&
          current.format?.encoding === next.format?.encoding &&
          current.formatEditable === next.formatEditable)
      )
        return;
      current = next;
      listeners.forEach((listener) => listener());
    },
  };
}

const formatLabel = { utf8: "UTF-8", utf8bom: "UTF-8 BOM" } as const;
const layoutKeys = ["KeyQ", "KeyW", "KeyE", "KeyR", "KeyT", "KeyY", "KeyZ"];

export function StatusBar({
  preferences,
  project,
  projectActions,
  branch,
  discipline,
  disciplineActions,
  connection,
  editorProblems,
  screenReader,
  onToggleScreenReader,
  terminalVisible,
  engineeringProblems,
  notices,
  onClearNotices,
  aiMode,
  onOpenAi,
  onShortcuts,
  onNewWindow,
  onReconnect,
  onSourceAction,
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
  editorProblems: { errors: number; warnings: number };
  screenReader: boolean;
  onToggleScreenReader: () => void;
  terminalVisible: boolean;
  engineeringProblems?: number;
  notices: StudioNotice[];
  onClearNotices: () => void;
  aiMode?: StudioAiSettings["mode"];
  onOpenAi: () => void;
  onShortcuts: () => void;
  onNewWindow: () => void;
  onReconnect: () => void;
  onSourceAction: (action: SourceStatusAction) => void;
  jobs: number;
  running: number;
  sourceStore: ReturnType<typeof createSourceStatusStore>;
  onBranch: () => void;
  onProblems: () => void;
  onTasks: () => void;
  onGoToLine: () => void;
  onHide: () => void;
}) {
  const sourceSnapshot = useSyncExternalStore(sourceStore.subscribe, sourceStore.get);
  const [terminalFocused, setTerminalFocused] = useState(false);
  useEffect(() => {
    const sync = (event: FocusEvent) => {
      if (!(event.target instanceof Element) || event.target.closest(".studio-statusbar")) return;
      setTerminalFocused(!!event.target.closest(".terminal-tab-content"));
    };
    window.addEventListener("focusin", sync);
    return () => window.removeEventListener("focusin", sync);
  }, []);
  const source = terminalVisible && terminalFocused ? undefined : sourceSnapshot;
  const bar = useRef<HTMLElement>(null);
  const origin = useRef<HTMLElement>(null);
  const [keyboardLabels, setKeyboardLabels] = useState<string>();
  const readKeyboardLayout = async () => {
    try {
      const keyboard = (
        navigator as Navigator & {
          keyboard?: { getLayoutMap: () => Promise<ReadonlyMap<string, string>> };
        }
      ).keyboard;
      const map = await keyboard?.getLayoutMap();
      setKeyboardLabels(map && layoutKeys.map((key) => map.get(key) ?? "–").join(" · "));
    } catch {
      setKeyboardLabels(undefined);
    }
  };
  const [point, setPoint] = useState<{ x: number; y: number }>();
  const openMenu = (x: number, target: EventTarget) => {
    origin.current = target instanceof Element ? target.closest("button") : null;
    setPoint({ x, y: bar.current!.getBoundingClientRect().top });
  };
  const show = (id: ItemId) => !preferences.hidden.includes(id);
  const connectionLabel =
    connection === "offline" ? t("连接中断") : connection === "online" ? t("本地") : t("读取中");
  const problemLabel = t("问题：{0} 个错误，{1} 个警告", {
    "0": editorProblems.errors,
    "1": editorProblems.warnings,
  });
  const engineeringLabel = t("当前工程问题：{0}", { "0": engineeringProblems ?? 0 });
  const aiLabel = t(aiMode === "disabled" ? "AI 未启用" : aiMode ? "AI 已配置" : "AI 连接");
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
          <NavigatorMenu
            label={t("远程主机")}
            title={connectionLabel}
            placement="start"
            direction="up"
            triggerClassName={`statusbar-item statusbar-connection ${connection}`}
            triggerIcon={
              <>
                <Icon name="remote" size={14} />
                <span className="statusbar-label">{connectionLabel}</span>
              </>
            }
            detail={<span>{t("Tex Studio 运行在本机。")}</span>}
            items={[
              ...(connection === "offline" ? [{ label: t("重试连接"), run: onReconnect }] : []),
              { label: t("新建窗口"), run: onNewWindow },
            ]}
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
        {show("problems") && project && (
          <button
            type="button"
            className="statusbar-item statusbar-problems"
            title={problemLabel}
            aria-label={problemLabel}
            onClick={onProblems}
          >
            <Icon name="error-circle" size={14} />
            {editorProblems.errors}
            <Icon name="warning" size={14} />
            {editorProblems.warnings}
          </button>
        )}
        {show("screenReader") && screenReader && (
          <button
            type="button"
            className="statusbar-item statusbar-screen-reader"
            title={t("屏幕阅读器模式已开启；点击恢复自动检测")}
            onClick={onToggleScreenReader}
          >
            {t("屏幕阅读器模式")}
          </button>
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
        {show("engineeringProblems") && engineeringProblems !== undefined && (
          <button
            type="button"
            className="statusbar-item statusbar-engineering"
            title={engineeringLabel}
            aria-label={engineeringLabel}
            onClick={onProblems}
          >
            <Icon name="system" size={14} />
            {engineeringProblems}
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
      <div className="statusbar-group statusbar-editor" aria-label={t("编辑器与通知")} role="group">
        {source && show("position") && (
          <button
            type="button"
            className="statusbar-item statusbar-position"
            title={t("转到行／列…")}
            onClick={onGoToLine}
          >
            {source.selections > 1
              ? t("{0} 个光标", { "0": source.selections })
              : t("行 {0}，列 {1}", { "0": source.line, "1": source.column })}
            {source.selected > 0 && (
              <span className="statusbar-selected">
                {t("（已选 {0} 个字符）", { "0": source.selected })}
              </span>
            )}
          </button>
        )}
        {source && show("indent") && (
          <NavigatorMenu
            label={t("编辑器缩进")}
            direction="up"
            triggerClassName="statusbar-item statusbar-indent"
            triggerIcon={
              <span>
                {t(source.insertSpaces ? "空格：{0}" : "制表符：{0}", { "0": source.tabSize })}
              </span>
            }
            items={[
              {
                label: t("使用空格缩进"),
                checked: source.insertSpaces,
                run: () => onSourceAction({ kind: "indent", insertSpaces: true }),
              },
              {
                label: t("使用制表符缩进"),
                checked: !source.insertSpaces,
                run: () => onSourceAction({ kind: "indent", insertSpaces: false }),
              },
              { label: "", separator: true },
              ...[2, 3, 4, 6, 8].map((tabSize) => ({
                label: t("缩进宽度：{0}", { "0": tabSize }),
                checked: source.tabSize === tabSize,
                run: () => onSourceAction({ kind: "indent", tabSize }),
              })),
              { label: "", separator: true },
              {
                label: t("将现有缩进转换为空格"),
                run: () => onSourceAction({ kind: "convertIndent", insertSpaces: true }),
              },
              {
                label: t("将现有缩进转换为制表符"),
                run: () => onSourceAction({ kind: "convertIndent", insertSpaces: false }),
              },
            ]}
          />
        )}
        {source?.format && show("encoding") && (
          <NavigatorMenu
            label={t("编辑器编码")}
            direction="up"
            triggerClassName="statusbar-item statusbar-encoding"
            triggerIcon={<span>{formatLabel[source.format.encoding]}</span>}
            detail={<span>{t("选择文件下次保存时使用的编码。")}</span>}
            items={(["utf8", "utf8bom"] as const).map((encoding) => ({
              label: formatLabel[encoding],
              checked: source.format?.encoding === encoding,
              run: source.formatEditable
                ? () => onSourceAction({ kind: "format", change: { encoding } })
                : undefined,
            }))}
          />
        )}
        {source?.format && show("lineEnding") && (
          <NavigatorMenu
            label={t("编辑器结束行")}
            title={t("更改文件行尾")}
            direction="up"
            triggerClassName="statusbar-item statusbar-line-ending"
            triggerIcon={
              <span>
                {source.format.lineEnding === "mixed" ? t("混合行尾") : source.format.lineEnding}
              </span>
            }
            items={(["LF", "CRLF"] as const).map((lineEnding) => ({
              label: lineEnding,
              checked: source.format?.lineEnding === lineEnding,
              run: source.formatEditable
                ? () => onSourceAction({ kind: "format", change: { lineEnding } })
                : undefined,
            }))}
          />
        )}
        {source && show("languageStatus") && (
          <NavigatorMenu
            label={t("编辑器语言状态")}
            direction="up"
            triggerClassName="statusbar-item statusbar-language-status"
            triggerIcon={<Icon name="braces" size={14} />}
            detail={
              <span>
                {t(
                  source.language === "json"
                    ? "JSON 诊断已启用"
                    : source.language === "plaintext"
                      ? "纯文本模式"
                      : "语法高亮已启用",
                )}
              </span>
            }
            items={[{ label: t("显示问题"), run: onProblems }]}
          />
        )}
        {source && show("language") && (
          <NavigatorMenu
            label={t("编辑器语言")}
            direction="up"
            triggerClassName="statusbar-item statusbar-language"
            triggerIcon={
              <span>
                {t(
                  sourceLanguages[source.language as keyof typeof sourceLanguages] ??
                    source.language,
                )}
              </span>
            }
            items={Object.entries(sourceLanguages).map(([language, label]) => ({
              label: t(label),
              checked: source.languageOverride === language,
              run: () => onSourceAction({ kind: "language", language }),
            }))}
          />
        )}
        {show("ai") && project && (
          <button
            type="button"
            className="statusbar-item statusbar-ai"
            title={aiLabel}
            aria-label={t("AI 状态：{0}", { "0": aiLabel })}
            onClick={onOpenAi}
          >
            <Icon name="sparkles" size={14} />
          </button>
        )}
        {show("keyboard") && (
          <NavigatorMenu
            label={t("键盘布局")}
            direction="up"
            triggerClassName="statusbar-item statusbar-keyboard"
            triggerIcon={<Icon name="keyboard" size={14} />}
            onOpen={() => void readKeyboardLayout()}
            detail={
              <span>
                {keyboardLabels
                  ? t("浏览器报告的键位：{0}", { "0": keyboardLabels })
                  : t("浏览器未提供键盘布局。")}
              </span>
            }
            items={[{ label: t("键盘快捷键参考…"), run: onShortcuts }]}
          />
        )}
        {show("notifications") && (
          <NavigatorMenu
            label={t("通知")}
            direction="up"
            triggerClassName="statusbar-item statusbar-notifications"
            triggerIcon={
              <>
                <Icon name="bell" size={14} />
                {notices.length || null}
              </>
            }
            detail={
              notices.length ? (
                <ul className="statusbar-notice-list">
                  {notices.map((notice) => (
                    <li key={notice.id}>
                      <Icon name={notice.error ? "error-circle" : "info"} size={14} />
                      <span>
                        {notice.message}
                        <small>{new Date(notice.time).toLocaleTimeString()}</small>
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <span>{t("本次会话暂无通知。")}</span>
              )
            }
            items={notices.length ? [{ label: t("清除全部通知"), run: onClearNotices }] : []}
          />
        )}
      </div>
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
            ...standardItems
              .filter(
                ({ id }) =>
                  source ||
                  ![
                    "position",
                    "indent",
                    "encoding",
                    "lineEnding",
                    "languageStatus",
                    "language",
                  ].includes(id),
              )
              .map(({ id, label }) => ({
                label: t(label),
                checkbox: true,
                checked: show(id),
                run: () => preferences.toggleItem(id),
              })),
            { label: "", separator: true },
            ...extensionItems.map(({ id, label }) => ({
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
