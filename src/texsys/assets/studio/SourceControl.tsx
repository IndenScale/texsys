import React, { useEffect, useState } from "react";
import { Icon } from "./ui";
import { FileIcon } from "./FileIcon";
import {
  commitGraph,
  gitGroups,
  type GitStatus,
  type GitEntry,
  type GitHistory,
  type GitDiff,
} from "./workspace-model";
import type { WorkbenchProps } from "./sdk";

export function SourceControl({
  scope,
  changeScope,
  status,
  busy,
  dirty,
  message,
  setMessage,
  action,
  openDiff,
  openFile,
  openCommit,
  api,
  refresh,
}: {
  scope: string;
  changeScope: (scope: string) => void;
  status: GitStatus;
  busy: boolean;
  dirty: boolean;
  message: string;
  setMessage: (value: string) => void;
  action: (action: string, entry?: GitEntry) => Promise<void>;
  openDiff: (entry: GitEntry, staged: boolean) => Promise<void>;
  openFile: (path: string) => unknown;
  openCommit: (diff: GitDiff) => void;
  api: WorkbenchProps["api"];
  refresh: () => Promise<void>;
}) {
  const groups = gitGroups(status);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [history, setHistory] = useState<GitHistory>();
  const [error, setError] = useState("");
  const [limit, setLimit] = useState(50);
  const [generation, setGeneration] = useState(0);
  const [selected, setSelected] = useState("");
  const [loading, setLoading] = useState(false);
  const [commitBusy, setCommitBusy] = useState(false);
  useEffect(() => {
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
  }, [api, status.snapshot, limit, generation]);
  const toggle = (id: string) => setCollapsed((prev) => ({ ...prev, [id]: !prev[id] }));
  const tool = (label: string, icon: string, handler: () => void, disabled = busy) => (
    <button
      type="button"
      className="core-tool"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={handler}
    >
      <Icon name={icon} size={15} />
    </button>
  );
  const canCommit = !busy && !!message.trim() && !!groups.staged.length && !status.truncated;
  const rows = commitGraph(history?.commits ?? []);
  const graphWidth = Math.max(
    24,
    ...rows.map((row) => Math.max(row.before.length, row.after.length) * 14 + 10),
  );
  return (
    <>
      <div className="scm-working">
        <label className="scm-scope-select">
          管理范围
          <select
            aria-label="源码管理范围"
            value={scope}
            disabled={busy || commitBusy}
            onChange={(event) => changeScope(event.target.value)}
          >
            <option value="project">当前项目</option>
            <option value="repository">整个 Git 仓库</option>
          </select>
        </label>
        <div className="core-scm-summary" title={status.repository}>
          <Icon name="branch" size={14} />
          <strong>{status.branch}</strong>
          {status.detached && <span>游离 HEAD</span>}
        </div>
        <p className="core-hint scm-scope" title={status.repository}>
          变更范围：{status.scope === "." ? "整个仓库" : status.scope}
        </p>
        <form
          className="core-commit"
          onSubmit={(event) => {
            event.preventDefault();
            if (canCommit) void action("commit");
          }}
        >
          <textarea
            aria-label="提交说明"
            placeholder="提交说明（⌘ / Ctrl + Enter 提交）"
            value={message}
            maxLength={4000}
            rows={2}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                event.preventDefault();
                if (canCommit) void action("commit");
              }
            }}
          />
          <button type="submit" className="core-primary" disabled={!canCommit}>
            <Icon name="tick" size={14} />
            {busy ? "正在操作…" : "提交已暂存的更改"}
          </button>
        </form>
        {(
          [
            ["已暂存的更改", groups.staged, true],
            ["更改", groups.changes, false],
          ] as const
        ).map(([label, entries, staged]) => (
          <div key={label} className="core-change-group">
            <div className="scm-section-heading">
              <button
                className="scm-section-toggle"
                aria-expanded={!collapsed[label]}
                onClick={() => toggle(label)}
              >
                <Icon name="chevron" expanded={!collapsed[label]} size={13} />
                <strong>{label}</strong>
                <small>{entries.length}</small>
              </button>
              {tool(
                staged ? "取消暂存全部更改" : "暂存全部更改",
                staged ? "minus" : "plus",
                () => void action(staged ? "unstage_all" : "stage_all"),
                busy || !entries.length || !!status.truncated,
              )}
            </div>
            {!collapsed[label] &&
              entries.map((entry) => (
                <div key={entry.path} className="core-change-row">
                  <button
                    title={`${entry.original ? `${entry.original} → ` : ""}${entry.path}`}
                    onClick={() => void openDiff(entry, staged)}
                  >
                    <FileIcon path={entry.path} />
                    <span>
                      {entry.path.split("/").pop()}
                      <small>{entry.path.split("/").slice(0, -1).join("/")}</small>
                    </span>
                  </button>
                  <div className="scm-row-tools">
                    {entry.worktree !== "D" &&
                      entry.index !== "D" &&
                      (!status.project_prefix ||
                        entry.path.startsWith(status.project_prefix + "/")) &&
                      tool(
                        `打开文件 ${entry.path}`,
                        "file",
                        () =>
                          void openFile(
                            status.project_prefix
                              ? entry.path.slice(status.project_prefix.length + 1)
                              : entry.path,
                          ),
                      )}
                    {!staged &&
                      tool(`放弃更改 ${entry.path}`, "undo", () => {
                        if (
                          window.confirm(
                            entry.index === "?"
                              ? `永久删除未跟踪文件“${entry.path}”？此操作也会关闭该文件的未保存编辑，无法撤销。`
                              : `放弃“${entry.path}”未暂存的更改？文件将恢复为暂存区版本，此操作也会关闭该文件的未保存编辑，无法撤销。`,
                          )
                        )
                          void action("discard", entry);
                      })}
                    {tool(
                      `${staged ? "取消暂存" : "暂存"} ${entry.path}`,
                      staged ? "minus" : "plus",
                      () => void action(staged ? "unstage" : "stage", entry),
                    )}
                  </div>
                  <b
                    title={staged ? "暂存区状态" : "工作区状态"}
                    className={`core-git-code code-${(staged ? entry.index : entry.worktree).trim()}`}
                  >
                    {staged ? entry.index : entry.worktree === "?" ? "U" : entry.worktree}
                  </b>
                </div>
              ))}
          </div>
        ))}
        {!status.entries.length && (
          <p className="core-hint">当前范围内工作区干净，没有待提交的更改。</p>
        )}
        {status.truncated && (
          <p className="core-hint">变更过多，列表已截断；请在终端处理部分变更。</p>
        )}
        {dirty && <p className="core-hint">有未保存的文件；暂存与提交使用磁盘内容。</p>}
      </div>
      <div className={`scm-graph ${collapsed.graph ? "collapsed" : ""}`}>
        <div className="scm-section-heading">
          <button
            className="scm-section-toggle"
            aria-label="提交历史图"
            aria-expanded={!collapsed.graph}
            onClick={() => toggle("graph")}
          >
            <Icon name="chevron" expanded={!collapsed.graph} size={13} />
            <strong>提交历史图</strong>
          </button>
          <span className="scm-graph-scope" title="显示整个仓库所有分支的提交拓扑">
            仓库
          </span>
          {tool(
            "刷新提交历史",
            "refresh",
            () => {
              setGeneration((value) => value + 1);
              void refresh();
            },
            loading,
          )}
        </div>
        {!collapsed.graph && (
          <div className="scm-graph-list" aria-label="仓库提交历史" aria-busy={loading}>
            {error && (
              <p className="core-error" role="alert">
                {error}
              </p>
            )}
            {!history && loading && <p className="core-hint">正在读取提交历史…</p>}
            {history?.commits.length === 0 && (
              <p className="core-hint">尚无提交。暂存文件并创建第一个提交。</p>
            )}
            {rows.map(({ commit, lane, before, after }) => (
              <button
                key={commit.id}
                className={`scm-graph-row ${selected === commit.id ? "selected" : ""}`}
                disabled={commitBusy}
                title={`${commit.subject}\n${commit.author} · ${new Date(commit.date).toLocaleString()}\n${commit.id}\n查看当前管理范围在此提交中的差异`}
                onClick={async () => {
                  setSelected(commit.id);
                  setCommitBusy(true);
                  try {
                    openCommit(
                      await api<GitDiff>(`/api/scm/commit?id=${commit.id}&scope=${scope}`),
                    );
                    setError("");
                  } catch (error) {
                    setError(String(error));
                  } finally {
                    setCommitBusy(false);
                  }
                }}
              >
                <svg width={graphWidth} height="29" aria-hidden="true" style={{ flexShrink: 0 }}>
                  {before.map((id, index) => (
                    <path
                      key={`in-${id}`}
                      d={`M${10 + index * 14},0 L${10 + index * 14},14`}
                      stroke={`var(--scm-lane-${index % 4})`}
                    />
                  ))}
                  {after.map((id, index) => {
                    const from = commit.parents.includes(id) ? lane : before.indexOf(id);
                    return (
                      <path
                        key={`out-${id}`}
                        d={`M${10 + from * 14},14 C${10 + from * 14},23 ${10 + index * 14},21 ${10 + index * 14},29`}
                        stroke={`var(--scm-lane-${index % 4})`}
                      />
                    );
                  })}
                  <circle
                    cx={10 + lane * 14}
                    cy="14"
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
                <span className="scm-commit-author">{commit.author}</span>
                {commit.refs.map((ref) => (
                  <span key={ref} className="scm-ref">
                    {ref.replace("HEAD -> ", "")}
                  </span>
                ))}
              </button>
            ))}
            {history?.truncated && (
              <button
                className="scm-load-more"
                disabled={loading || limit >= 500}
                onClick={() => setLimit((value) => Math.min(500, value + 50))}
              >
                {limit >= 500 ? "已显示 500 条，请在终端查看更早历史" : "加载更多提交"}
              </button>
            )}
          </div>
        )}
      </div>
    </>
  );
}
