import { getLocale, t } from "./i18n";
import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./ui";
import { FileIcon } from "./FileIcon";
import { NavigatorButton, NavigatorToolbar } from "./NavigatorPanel";
import { NavigatorMenu } from "./NavigatorMenu";
import { TooltipButton } from "./ToolbarButton";
import {
  commitGraph,
  gitGroups,
  gitPathInProject,
  type GitStatus,
  type GitEntry,
  type GitHistory,
  type GitDiff,
} from "./workspace-model";
import type { WorkbenchProps } from "./sdk";
import type { StudioAiSettings } from "./ai-settings";

type DraftGeneration =
  | { phase: "loading" | "generating" }
  | { phase: "review"; destination: Omit<StudioAiSettings, "hasApiKey">; context: object }
  | { phase: "error" | "changed"; message: string };

export function SourceControl({
  status,
  scope,
  active,
  activeDiff,
  busy,
  dirty,
  message,
  setMessage,
  action,
  openDiff,
  openFile,
  openCommit,
  api,
  refreshGeneration,
  refresh,
  graphExpanded,
  setGraphExpanded,
}: {
  status: GitStatus;
  scope: "project" | "repository";
  active: boolean;
  activeDiff?: GitDiff;
  busy: boolean;
  dirty: boolean;
  message: string;
  setMessage: (value: string) => void;
  action: (action: string, entry?: GitEntry) => Promise<void>;
  openDiff: (entry: GitEntry, staged: boolean) => Promise<void>;
  openFile: (path: string) => unknown;
  openCommit: (id: string) => Promise<void>;
  api: WorkbenchProps["api"];
  refreshGeneration: number;
  refresh: () => void;
  graphExpanded: boolean;
  setGraphExpanded: (expanded: boolean) => void;
}) {
  const groups = useMemo(() => {
    const directory = (path: string) => path.slice(0, path.lastIndexOf("/") + 1);
    const entries = [...status.entries].sort(
      (a, b) => directory(a.path).localeCompare(directory(b.path)) || a.path.localeCompare(b.path),
    );
    return gitGroups({ ...status, entries });
  }, [status]);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [commitAttempted, setCommitAttempted] = useState(false);
  const messageInput = useRef<HTMLTextAreaElement>(null);
  const generationHintId = useId();
  const draftContext = useRef<object | null>(null);
  const [generation, setGeneration] = useState<DraftGeneration>();
  useLayoutEffect(() => {
    draftContext.current = {};
    return () => {
      draftContext.current = null;
    };
  }, [status.snapshot, scope, message]);
  const [history, setHistory] = useState<GitHistory>();
  const [error, setError] = useState("");
  const [limit, setLimit] = useState(50);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!active || !graphExpanded) return;
    let live = true;
    setLoading(true);
    api<GitHistory>(`/api/scm/history?limit=${limit}`)
      .then((result) => {
        if (live) {
          setHistory(result);
          setError("");
        }
      })
      .catch((error) => {
        if (live) setError(String(error));
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [active, graphExpanded, api, status.snapshot, limit, refreshGeneration]);
  const toggle = (id: string) => setCollapsed((prev) => ({ ...prev, [id]: !prev[id] }));
  const tool = (
    label: string,
    icon: string,
    onClick: () => void,
    disabled = busy,
    title = label,
  ) => (
    <NavigatorButton
      label={label}
      icon={icon}
      onClick={onClick}
      disabled={disabled}
      title={title}
    />
  );
  const hasChanges = status.entries.length > 0;
  const commitHint = !groups.staged.length
    ? t("暂存文件后即可提交。")
    : !message.trim()
      ? t("请输入提交说明。")
      : "";
  const submitCommit = () => {
    if (busy || status.truncated) return;
    if (commitHint) {
      if (collapsed.changes) toggle("changes");
      setCommitAttempted(true);
      if (groups.staged.length) messageInput.current?.focus();
      return;
    }
    setCommitAttempted(false);
    void action("commit");
  };
  let generationBlocker = "";
  if (generation?.phase === "loading") generationBlocker = "正在读取 AI 设置…";
  else if (generation?.phase === "generating") generationBlocker = "正在生成提交说明…";
  else if (busy) generationBlocker = "正在操作…";
  else if (status.truncated) generationBlocker = "变更数量超过显示上限，请先在终端处理部分变更。";
  else if (!groups.staged.length)
    generationBlocker = "没有暂存的更改。点击文件旁的 + 暂存，再生成提交说明。";
  const canGenerate = !generationBlocker;
  const draftChanged = () =>
    setGeneration({ phase: "changed", message: t("提交说明或暂存区已变化，请重新生成。") });
  const draftError = (error: unknown) =>
    setGeneration({
      phase: "error",
      message: error instanceof Error ? error.message : String(error),
    });
  const reviewGeneration = async () => {
    const context = draftContext.current;
    if (!canGenerate || !context) return;
    setGeneration({ phase: "loading" });
    try {
      const { mode, baseUrl, model, hasApiKey } = await api<StudioAiSettings>("/api/ai/settings");
      if (!draftContext.current) return;
      if (draftContext.current !== context) return draftChanged();
      if (mode === "disabled") {
        setGeneration({ phase: "error", message: t("请在设置中配置 AI 连接。") });
        return;
      }
      if (mode === "byok" && !hasApiKey) {
        setGeneration({ phase: "error", message: t("请先在设置中保存 API 密钥。") });
        return;
      }
      setGeneration({ phase: "review", context, destination: { mode, baseUrl, model } });
    } catch (error) {
      if (draftContext.current) draftError(error);
    }
  };
  const generateMessage = async () => {
    if (generation?.phase !== "review" || !canGenerate) return;
    const { context, destination } = generation;
    if (draftContext.current !== context) return draftChanged();
    setGeneration({ phase: "generating" });
    try {
      const draft = await api<{ message: string }>("/api/ai/commit-message", {
        snapshot: status.snapshot,
        scope,
        locale: getLocale(),
        destination,
      });
      if (!draftContext.current) return;
      if (draftContext.current !== context) return draftChanged();
      setGeneration(undefined);
      setMessage(draft.message);
      setCommitAttempted(false);
      messageInput.current?.focus();
    } catch (error) {
      if (draftContext.current) draftError(error);
    }
  };
  const shortcut = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘Enter" : "Ctrl+Enter";
  const branch = status.detached ? t("游离 HEAD") : status.branch;
  const messagePlaceholder = t("消息（{0} 提交到“{1}”）", {
    "0": shortcut,
    "1": branch ?? "HEAD",
  });
  const generateLabel = t("使用 AI 生成提交说明");
  const generationHint = t(generationBlocker);
  const rows = useMemo(() => commitGraph(history?.commits ?? []), [history]);
  const graphWidth = Math.max(
    20,
    ...rows.map((row) => Math.max(row.before.length, row.after.length) * 14 + 6),
  );
  return (
    <>
      <div className="scm-working">
        <div className="scm-section-heading scm-changes-heading">
          <button
            type="button"
            className="scm-section-toggle"
            aria-expanded={!collapsed.changes}
            onClick={() => toggle("changes")}
          >
            <Icon name="scm-chevron" size={14} />
            <strong>{t("更改")}</strong>
          </button>
          <NavigatorToolbar role="toolbar" aria-label={t("更改操作")}>
            {tool(t("提交"), "tick", submitCommit, busy || !hasChanges || !!status.truncated)}
            {tool(t("刷新源代码管理"), "refresh", refresh)}
            <NavigatorMenu
              label={t("更多更改操作")}
              items={[
                {
                  label: t("暂存全部更改"),
                  run:
                    !busy && !status.truncated && groups.changes.length
                      ? () => void action("stage_all")
                      : undefined,
                },
                {
                  label: t("取消暂存全部更改"),
                  run:
                    !busy && !status.truncated && groups.staged.length
                      ? () => void action("unstage_all")
                      : undefined,
                },
              ]}
            />
          </NavigatorToolbar>
        </div>
        {!collapsed.changes && hasChanges && (
          <form
            className="core-commit"
            onSubmit={(event) => {
              event.preventDefault();
              submitCommit();
            }}
          >
            <div className="scm-message">
              <textarea
                ref={messageInput}
                aria-label={t("提交说明")}
                placeholder={messagePlaceholder}
                aria-keyshortcuts="Meta+Enter Control+Enter"
                title={messagePlaceholder}
                value={message}
                maxLength={4000}
                rows={1}
                onChange={(event) => {
                  setMessage(event.target.value);
                  setCommitAttempted(false);
                }}
                onKeyDown={(event) => {
                  if (
                    (event.metaKey || event.ctrlKey) &&
                    event.key === "Enter" &&
                    !event.nativeEvent.isComposing
                  ) {
                    event.preventDefault();
                    submitCommit();
                  }
                }}
              />
              <TooltipButton
                className="core-primary scm-generate"
                data-expanded={!message}
                type="button"
                aria-label={generateLabel}
                aria-describedby={canGenerate ? undefined : generationHintId}
                tooltip={generationHint || generateLabel}
                aria-disabled={!canGenerate || undefined}
                onClick={() => void reviewGeneration()}
              >
                {!message && t("AI 生成")}
                <Icon name="sparkles" size={16} />
              </TooltipButton>
            </div>
            <p id={generationHintId} className="sr-only" role="status">
              {generationHint}
            </p>
            {generation?.phase === "review" && (
              <div className="scm-ai-review">
                <p className="scm-commit-hint">
                  {t("将{0}的暂存差异发送至 {1}，使用 {2} 生成草稿。", {
                    "0": t(scope === "project" ? "当前项目" : "整个仓库"),
                    "1": generation.destination.baseUrl,
                    "2": generation.destination.model,
                  })}
                  {message && <> {t("生成成功后将替换当前提交说明。")}</>}
                </p>
                <div className="scm-ai-review-actions">
                  <TooltipButton
                    type="button"
                    className="core-primary"
                    aria-describedby={canGenerate ? undefined : generationHintId}
                    tooltip={generationHint || t("发送并生成")}
                    aria-disabled={!canGenerate || undefined}
                    onClick={() => void generateMessage()}
                  >
                    {t("发送并生成")}
                  </TooltipButton>
                  <button type="button" onClick={() => setGeneration(undefined)}>
                    {t("取消")}
                  </button>
                </div>
              </div>
            )}
            {(generation?.phase === "error" || generation?.phase === "changed") && (
              <p
                className={generation.phase === "error" ? "core-error" : "scm-commit-hint"}
                role={generation.phase === "error" ? "alert" : "status"}
              >
                {generation.message}
              </p>
            )}
            <div className="scm-commit-split">
              <button
                type="submit"
                className="core-primary scm-commit-submit"
                disabled={busy || !!status.truncated}
              >
                <Icon name="tick" size={16} />
                {busy ? t("正在操作…") : t("提交")}
              </button>
              <NavigatorMenu
                label={t("提交选项")}
                placement="side"
                triggerClassName="core-primary scm-commit-menu-trigger"
                triggerIcon={<Icon name="scm-chevron" size={18} />}
                items={[
                  { label: t("提交"), run: busy || status.truncated ? undefined : submitCommit },
                  { label: t("提交（修订）"), reason: t("Tex Studio 暂不支持修订提交。") },
                  {
                    label: t("提交并推送"),
                    reason: t("Tex Studio 暂不支持远程 Git 操作。"),
                    separator: true,
                  },
                  { label: t("提交并同步"), reason: t("Tex Studio 暂不支持远程 Git 操作。") },
                ]}
              />
            </div>
            {commitAttempted && commitHint && (
              <p className="scm-commit-hint" role="status">
                {commitHint}
              </p>
            )}
          </form>
        )}
        <div className="scm-file-list" hidden={collapsed.changes}>
          {(
            [
              ["staged", t("已暂存的更改"), groups.staged, true],
              ["working", t("更改"), groups.changes, false],
            ] as const
          )
            .filter(([, , entries]) => entries.length > 0)
            .map(([id, label, entries, staged]) => (
              <div key={id} className="core-change-group">
                <div className="scm-section-heading scm-group-heading">
                  <button
                    className="scm-section-toggle"
                    aria-expanded={!collapsed[id]}
                    onClick={() => toggle(id)}
                  >
                    <Icon name="scm-chevron" size={14} />
                    <strong>{label}</strong>
                    <small className="scm-count">{entries.length}</small>
                  </button>
                  {tool(
                    staged ? t("取消暂存全部更改") : t("暂存全部更改"),
                    staged ? "minus" : "plus",
                    () => void action(staged ? "unstage_all" : "stage_all"),
                    busy || !!status.truncated,
                  )}
                </div>
                {!collapsed[id] && (
                  <div className="scm-group-rows">
                    {entries.map((entry) => {
                      const filePath = gitPathInProject(entry.path, status.project_prefix);
                      return (
                        <div key={entry.path} className="core-change-row">
                          <button
                            aria-current={
                              activeDiff &&
                              !activeDiff.historical &&
                              activeDiff.path === entry.path &&
                              activeDiff.staged === staged
                                ? "true"
                                : undefined
                            }
                            title={`${entry.original ? `${entry.original} → ` : ""}${entry.path}`}
                            aria-label={entry.path}
                            onClick={() => void openDiff(entry, staged)}
                          >
                            <FileIcon path={entry.path} size={14} />
                            <span className="scm-file-label">
                              <span>{entry.path.split("/").pop()}</span>
                              {entry.path.includes("/") && (
                                <small>{entry.path.slice(0, entry.path.lastIndexOf("/"))}</small>
                              )}
                            </span>
                          </button>
                          <div className="scm-row-tools">
                            {entry.worktree !== "D" &&
                              entry.index !== "D" &&
                              tool(
                                t("打开文件 {0}", { "0": entry.path }),
                                "scm-open-file",
                                () => {
                                  if (filePath !== undefined) void openFile(filePath);
                                },
                                busy || filePath === undefined,
                                filePath === undefined ? t("此文件不在当前项目中。") : undefined,
                              )}
                            {!staged &&
                              tool(
                                t("放弃更改 {0}", { "0": entry.path }),
                                "undo",
                                () => {
                                  if (
                                    window.confirm(
                                      entry.index === "?"
                                        ? t(
                                            "永久删除未跟踪文件“{0}”？此操作也会关闭该文件的未保存编辑，无法撤销。",
                                            { "0": entry.path },
                                          )
                                        : t(
                                            "放弃“{0}”未暂存的更改？文件将恢复为暂存区版本，此操作也会关闭该文件的未保存编辑，无法撤销。",
                                            { "0": entry.path },
                                          ),
                                    )
                                  )
                                    void action("discard", entry);
                                },
                                busy || !!status.truncated,
                              )}
                            {tool(
                              `${staged ? t("取消暂存") : t("暂存")} ${entry.path}`,
                              staged ? "minus" : "plus",
                              () => void action(staged ? "unstage" : "stage", entry),
                              busy || !!status.truncated,
                            )}
                          </div>
                          <span
                            title={staged ? t("暂存区状态") : t("工作区状态")}
                            className={`core-git-code code-${(staged ? entry.index : entry.worktree).trim()}`}
                          >
                            {staged ? entry.index : entry.worktree === "?" ? "U" : entry.worktree}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
          {!hasChanges && (
            <p className="scm-clean">
              <Icon name="tick" size={14} />
              {t("当前范围没有更改。")}
            </p>
          )}
          {status.truncated && (
            <p className="core-hint">{t("变更过多，列表已截断；请在终端处理部分变更。")}</p>
          )}
          {dirty && <p className="core-hint">{t("有未保存的文件；暂存与提交使用磁盘内容。")}</p>}
        </div>
      </div>
      <div className={`scm-graph ${graphExpanded ? "" : "collapsed"}`}>
        <div className="scm-section-heading">
          <button
            className="scm-section-toggle"
            aria-expanded={graphExpanded}
            title={t("仓库历史")}
            onClick={() => setGraphExpanded(!graphExpanded)}
          >
            <Icon name="scm-chevron" size={14} />
            <strong>{t("图谱")}</strong>
          </button>
        </div>
        {graphExpanded && (
          <div className="scm-graph-list" aria-label={t("仓库提交历史")} aria-busy={loading}>
            {error && (
              <p className="core-error" role="alert">
                {error}
              </p>
            )}
            {!history && loading && <p className="core-hint">{t("正在读取提交历史…")}</p>}
            {history?.commits.length === 0 && (
              <p className="core-hint">{t("尚无提交。暂存文件并创建第一个提交。")}</p>
            )}
            {rows.map(({ commit, lane, before, after }) => (
              <button
                key={commit.id}
                className="scm-graph-row"
                aria-current={
                  activeDiff?.historical && activeDiff.path === commit.id.slice(0, 8)
                    ? "true"
                    : undefined
                }
                title={t("{0}\n{1} · {2}\n{3}\n查看当前管理范围在此提交中的差异", {
                  "0": commit.subject,
                  "1": commit.author,
                  "2": new Date(commit.date).toLocaleString(),
                  "3": commit.id,
                })}
                onClick={() => void openCommit(commit.id)}
              >
                <svg width={graphWidth} height="24" aria-hidden="true" style={{ flexShrink: 0 }}>
                  {before.map((id, index) => (
                    <path
                      key={`in-${id}`}
                      d={`M${10 + index * 14},0 L${10 + index * 14},12`}
                      stroke={`var(--scm-lane-${index % 4})`}
                    />
                  ))}
                  {after.map((id, index) => {
                    const from = commit.parents.includes(id) ? lane : before.indexOf(id);
                    return (
                      <path
                        key={`out-${id}`}
                        d={`M${10 + from * 14},12 C${10 + from * 14},18 ${10 + index * 14},18 ${10 + index * 14},24`}
                        stroke={`var(--scm-lane-${index % 4})`}
                      />
                    );
                  })}
                  <circle
                    cx={10 + lane * 14}
                    cy="12"
                    r={commit.refs.some((ref) => ref.startsWith("HEAD")) ? 4.5 : 3.5}
                    fill={
                      commit.refs.some((ref) => ref.startsWith("HEAD"))
                        ? "var(--ui-workspace-chrome)"
                        : `var(--scm-lane-${lane % 4})`
                    }
                    stroke={`var(--scm-lane-${lane % 4})`}
                    strokeWidth="2"
                  />
                </svg>
                <span className="scm-commit-subject">{commit.subject}</span>
                {commit.refs.length > 0 && (
                  <span className="scm-commit-refs">
                    {commit.refs.map((ref) => (
                      <span key={ref} className="scm-ref" title={ref}>
                        {ref.replace("HEAD -> ", "")}
                      </span>
                    ))}
                  </span>
                )}
              </button>
            ))}
            {history?.truncated && (
              <button
                className="scm-load-more"
                disabled={loading || limit >= 500}
                onClick={() => setLimit((value) => Math.min(500, value + 50))}
              >
                {limit >= 500 ? t("已显示 500 条，请在终端查看更早历史") : t("加载更多提交")}
              </button>
            )}
          </div>
        )}
      </div>
    </>
  );
}
