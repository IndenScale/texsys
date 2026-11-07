import React, { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { FileIcon } from "./FileIcon";
import { Icon } from "./ui";
import type { WorkbenchProps } from "./sdk";
import type { GitEntry, SearchLine, SearchMatch, SearchResults } from "./workspace-model";
import { groupCodeSearchResults, searchLineSegments } from "./code-search-model";
import "./code-search.css";

type ReplacementRequest = {
  query: string;
  replacement: string;
  case_sensitive: boolean;
  whole_word: boolean;
  regex: boolean;
  include: string;
  exclude: string;
  preserve_case: boolean;
  paths?: string[];
};
type ReplacementPreview = {
  snapshot: string;
  count: number;
  skipped: number;
  changes: { path: string; count: number; before: string; after: string; line: number }[];
};
function SearchTool({
  label,
  icon,
  onClick,
  disabled,
  pressed,
}: {
  label: string;
  icon: string;
  onClick: () => void;
  disabled?: boolean;
  pressed?: boolean;
}) {
  return (
    <button
      className="core-tool"
      aria-label={label}
      title={label}
      disabled={disabled}
      aria-pressed={pressed}
      onClick={onClick}
    >
      <Icon name={icon} size={16} />
    </button>
  );
}
function SearchToggle({
  label,
  pressed,
  onClick,
  children,
}: {
  label: string;
  pressed: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      className="code-search-toggle"
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
function ReplacementDialog({
  preview,
  request,
  busy,
  error,
  onClose,
  onApply,
}: {
  preview: ReplacementPreview;
  request: ReplacementRequest;
  busy: boolean;
  error: string;
  onClose: () => void;
  onApply: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const busyRef = useRef(busy);
  busyRef.current = busy;
  useEffect(() => {
    const origin = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) {
        event.preventDefault();
        onClose();
      }
      if (event.key === "Tab") {
        const nodes = Array.from(
          ref.current?.querySelectorAll<HTMLElement>("button:not(:disabled),[tabindex='0']") ?? [],
        );
        if (!nodes.length) return;
        if (event.shiftKey && document.activeElement === nodes[0]) {
          event.preventDefault();
          nodes.at(-1)?.focus();
        } else if (!event.shiftKey && document.activeElement === nodes.at(-1)) {
          event.preventDefault();
          nodes[0].focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => {
      document.removeEventListener("keydown", handler);
      origin?.focus();
    };
  }, []);
  return (
    <div className="core-dialog-backdrop">
      <div
        ref={ref}
        className="core-dialog code-replace-preview"
        role="dialog"
        aria-modal="true"
        aria-label="预览替换"
      >
        <strong>预览替换</strong>
        <p>
          将“{request.query}”替换为“{request.replacement || "（空文本）"}” · {preview.count} 处 ·{" "}
          {preview.changes.length} 个文件
        </p>
        <div className="code-replace-files" tabIndex={0}>
          {preview.changes.map((change) => (
            <section key={change.path}>
              <div>
                <FileIcon path={change.path} />
                <strong>{change.path}</strong>
                <span>{change.count} 处</span>
              </div>
              <pre className="before">− {change.before || " "}</pre>
              <pre className="after">+ {change.after || " "}</pre>
            </section>
          ))}
        </div>
        <p className="core-hint">
          每个文件展示首处修改附近的内容。确认后写入磁盘；文件有外部修改时会要求重新预览。
        </p>
        {error && (
          <p className="code-search-error" role="alert">
            {error}
          </p>
        )}
        <div className="core-dialog-actions">
          <button disabled={busy} onClick={onClose}>
            取消
          </button>
          <button
            className="core-primary"
            disabled={busy || !preview.changes.length || !!error}
            onClick={onApply}
          >
            {busy ? "正在替换…" : `替换 ${preview.count} 处`}
          </button>
        </div>
      </div>
    </div>
  );
}

export function useCodeSearch({
  api,
  revision,
  generation,
  inputRef,
  onOpenFile,
  onOpenEditor,
  onChanged,
  hasDirtyFile,
  dirty,
  gitEntries,
  notify,
}: {
  api: WorkbenchProps["api"];
  revision: string;
  generation: number;
  inputRef: RefObject<HTMLInputElement | null>;
  onOpenFile: (path: string, match?: SearchMatch) => void;
  onOpenEditor: () => void;
  onChanged: (paths: string[]) => Promise<void>;
  hasDirtyFile: (path: string) => boolean;
  dirty: boolean;
  gitEntries: GitEntry[];
  notify: WorkbenchProps["notify"];
}) {
  const [query, setQuery] = useState(""),
    [replaceText, setReplaceText] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false),
    [wholeWord, setWholeWord] = useState(false),
    [regex, setRegex] = useState(false),
    [preserveCase, setPreserveCase] = useState(false);
  const [replaceOpen, setReplaceOpen] = useState(true),
    [detailsOpen, setDetailsOpen] = useState(false);
  const [include, setInclude] = useState(""),
    [exclude, setExclude] = useState("");
  const [results, setResults] = useState<SearchResults>(),
    [error, setError] = useState(""),
    [searching, setSearching] = useState(false),
    [run, setRun] = useState(0);
  const [collapsed, setCollapsed] = useState(new Set<string>()),
    [grouped, setGrouped] = useState(true),
    [selected, setSelected] = useState("");
  const [replaceError, setReplaceError] = useState("");
  const [replacementBusy, setReplacementBusy] = useState(false),
    [replacementPreview, setReplacementPreview] = useState<{
      request: ReplacementRequest;
      result: ReplacementPreview;
    }>();
  const groups = useMemo(() => groupCodeSearchResults(results), [results]);
  const occurrenceCount = results?.matches.length ?? 0;
  const searchOptions = {
    query,
    replacement: replaceText,
    case_sensitive: caseSensitive,
    whole_word: wholeWord,
    regex,
    include,
    exclude,
    preserve_case: preserveCase,
  };
  const gitStatuses = new Map(
    gitEntries.map((entry) => [entry.path, entry.index === " " ? entry.worktree : entry.index]),
  );
  useEffect(() => {
    if (!query) {
      setResults(undefined);
      setError("");
      setSearching(false);
      return;
    }
    let valid = true;
    setSearching(true);
    setError("");
    setResults(undefined);
    const timer = setTimeout(() => {
      const params = new URLSearchParams({
        q: query,
        case: String(caseSensitive),
        word: String(wholeWord),
        regex: String(regex),
        include,
        exclude,
      });
      void api<SearchResults>(`/api/search?${params}`)
        .then((value) => {
          if (valid) setResults(value);
        })
        .catch((error) => {
          if (valid) setError(error instanceof Error ? error.message : String(error));
        })
        .finally(() => {
          if (valid) setSearching(false);
        });
    }, 250);
    return () => {
      valid = false;
      clearTimeout(timer);
    };
  }, [query, caseSensitive, wholeWord, regex, include, exclude, run, generation, revision, api]);
  useEffect(() => {
    setCollapsed(new Set());
    setSelected("");
    setReplaceError("");
  }, [query, caseSensitive, wholeWord, regex, include, exclude]);
  const navigate = (line: SearchLine, match = line.matches[0]) => {
    if (!match) return;
    setSelected(`${line.path}:${line.line}:${match.column}`);
    onOpenFile(line.path, {
      path: line.path,
      line: line.line,
      column: match.column,
      end_column: match.end_column,
      text: line.text,
      offset: match.offset,
      length: match.length,
    });
  };
  const previewReplacement = async (paths?: string[]) => {
    if (!query || replacementBusy || !occurrenceCount) return;
    const request = { ...searchOptions, ...(paths ? { paths } : {}) };
    setReplacementBusy(true);
    setReplaceError("");
    try {
      const result = await api<ReplacementPreview>("/api/search/replace", request);
      const dirtyPath = result.changes.find((change) => hasDirtyFile(change.path))?.path;
      if (dirtyPath) throw new Error(`${dirtyPath} 有未保存编辑，请先保存或关闭该文件。`);
      if (!result.changes.length) {
        notify("没有需要替换的内容。");
        return;
      }
      setReplacementPreview({ request, result });
    } catch (error) {
      setReplaceError(error instanceof Error ? error.message : String(error));
    } finally {
      setReplacementBusy(false);
    }
  };
  const applyReplacement = async () => {
    if (!replacementPreview || replacementBusy) return;
    setReplacementBusy(true);
    try {
      const dirtyPath = replacementPreview.result.changes.find((change) =>
        hasDirtyFile(change.path),
      )?.path;
      if (dirtyPath) throw new Error(`${dirtyPath} 有未保存编辑，请先保存。`);
      const result = await api<{
        complete: boolean;
        applied: string[];
        error?: string;
        count: number;
      }>("/api/search/replace", {
        ...replacementPreview.request,
        snapshot: replacementPreview.result.snapshot,
        apply: true,
      });
      await onChanged(result.applied);
      setReplacementPreview(undefined);
      setRun((value) => value + 1);
      if (!result.complete)
        throw new Error(`已修改 ${result.applied.length} 个文件，其余未完成：${result.error}`);
      notify(`已替换 ${result.count} 处，涉及 ${result.applied.length} 个文件。`);
    } catch (error) {
      setReplaceError(error instanceof Error ? error.message : String(error));
      notify(error instanceof Error ? error.message : String(error), true);
    } finally {
      setReplacementBusy(false);
    }
  };
  const reset = () => {
    setQuery("");
    setReplaceText("");
    setInclude("");
    setExclude("");
    setCaseSensitive(false);
    setWholeWord(false);
    setRegex(false);
    setPreserveCase(false);
    setResults(undefined);
    inputRef.current?.focus();
  };
  const toggleFile = (path: string) =>
    setCollapsed((previous) => {
      const next = new Set(previous);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  const resultRow = (line: SearchLine, flat = false) => (
    <div key={`${line.path}:${line.line}`} className="code-search-result-row">
      <button
        role="treeitem"
        aria-level={grouped ? 2 : 1}
        aria-selected={selected.startsWith(`${line.path}:${line.line}:`)}
        className="code-search-result"
        title={`${line.path}:${line.line} · ${line.count} 处匹配`}
        onClick={() => navigate(line)}
      >
        <span className="code-search-line" aria-hidden="true">
          {line.line}
        </span>
        <span className="code-search-snippet">
          {flat && <span className="code-result-path">{line.path} </span>}
          {line.prefix && "…"}
          {searchLineSegments(line).map((segment, index) =>
            segment.match ? (
              <mark
                key={index}
                onClick={(event) => {
                  event.stopPropagation();
                  navigate(line, segment.match);
                }}
              >
                {segment.text}
              </mark>
            ) : (
              <React.Fragment key={index}>{segment.text}</React.Fragment>
            ),
          )}
          {line.suffix && "…"}
        </span>
      </button>
    </div>
  );
  const resultTree = (
    <div
      className="code-search-result-tree"
      role="tree"
      aria-label="代码搜索结果"
      onKeyDown={(event) => {
        const row = (event.target as Element).closest<HTMLButtonElement>('[role="treeitem"]');
        if (!row) return;
        const rows = Array.from(
          event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="treeitem"]'),
        ).filter((item) => !item.closest("[hidden]"));
        const index = rows.indexOf(row),
          path = row.dataset.file;
        if (event.key === "ArrowLeft" && path && !collapsed.has(path)) {
          event.preventDefault();
          toggleFile(path);
          return;
        }
        if (event.key === "ArrowRight" && path && collapsed.has(path)) {
          event.preventDefault();
          toggleFile(path);
          return;
        }
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? rows.length - 1
              : Math.max(
                  0,
                  Math.min(rows.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)),
                );
        rows[next]?.focus();
      }}
    >
      {grouped
        ? groups.map((group) => (
            <div key={group.path} role="group" className="code-search-file">
              <div className="code-search-file-heading">
                <button
                  role="treeitem"
                  aria-level={1}
                  aria-expanded={!collapsed.has(group.path)}
                  data-file={group.path}
                  title={group.path}
                  onClick={() => toggleFile(group.path)}
                >
                  <Icon name="chevron" size={14} expanded={!collapsed.has(group.path)} />
                  <FileIcon path={group.path} />
                  <span className="code-search-file-name">{group.path.split("/").pop()}</span>
                  {group.path.includes("/") && (
                    <span className="code-search-parent">
                      {group.path.split("/").slice(0, -1).join("/")}
                    </span>
                  )}
                  {gitStatuses.get(group.path) && (
                    <span className="code-search-git">
                      {gitStatuses.get(group.path) === "?" ? "U" : gitStatuses.get(group.path)}
                    </span>
                  )}
                  <span className="code-search-count">{group.count}</span>
                </button>
                {replaceOpen && (
                  <SearchTool
                    label={`替换文件 ${group.path}`}
                    icon="replace"
                    disabled={replacementBusy || searching}
                    onClick={() => void previewReplacement([group.path])}
                  />
                )}
              </div>
              <div role="group" hidden={collapsed.has(group.path)}>
                {group.lines.map((line) => resultRow(line))}
              </div>
            </div>
          ))
        : groups.flatMap((group) => group.lines.map((line) => resultRow(line, true)))}
    </div>
  );
  const status = searching
    ? "正在搜索…"
    : results
      ? `${occurrenceCount} 个结果，${groups.length} 个文件`
      : "搜索项目中的代码与文本";
  const header = (
    <div className="core-heading code-search-heading">
      <strong>Code Search</strong>
      <div className="core-tools">
        <SearchTool
          label="刷新搜索"
          icon="refresh"
          onClick={() => setRun((value) => value + 1)}
          disabled={!query || searching}
        />
        <SearchTool
          label="清空搜索"
          icon="clear-search"
          onClick={() => {
            setQuery("");
            inputRef.current?.focus();
          }}
          disabled={!query}
        />
        <SearchTool
          label="在编辑器中打开搜索"
          icon="new-file"
          onClick={onOpenEditor}
          disabled={!occurrenceCount || searching}
        />
        <SearchTool
          label="切换平铺结果"
          icon="list"
          pressed={!grouped}
          onClick={() => setGrouped(!grouped)}
          disabled={!groups.length}
        />
        <SearchTool
          label="折叠所有搜索结果"
          icon="collapse"
          onClick={() => setCollapsed(new Set(groups.map((group) => group.path)))}
          disabled={!groups.length || !grouped}
        />
      </div>
    </div>
  );
  const controls = (
    <div className="code-search-controls">
      <div className="code-query-row">
        <button
          className="code-replace-toggle"
          aria-label={replaceOpen ? "隐藏替换" : "显示替换"}
          aria-expanded={replaceOpen}
          onClick={() => setReplaceOpen(!replaceOpen)}
        >
          <Icon name="chevron" size={14} expanded={replaceOpen} />
        </button>
        <div className={`code-search-box ${error ? "has-error" : ""}`}>
          <input
            ref={inputRef}
            aria-label="搜索项目内容"
            aria-invalid={!!error}
            aria-describedby={error ? "code-search-error" : undefined}
            placeholder="Search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") setRun((value) => value + 1);
              if (event.key === "Escape") {
                setQuery("");
              }
            }}
          />
          <div className="code-search-switches">
            <SearchToggle
              label="区分大小写"
              pressed={caseSensitive}
              onClick={() => setCaseSensitive(!caseSensitive)}
            >
              Aa
            </SearchToggle>
            <SearchToggle
              label="全字匹配"
              pressed={wholeWord}
              onClick={() => setWholeWord(!wholeWord)}
            >
              <span className="code-whole-word">ab</span>
            </SearchToggle>
            <SearchToggle label="使用正则表达式" pressed={regex} onClick={() => setRegex(!regex)}>
              <span className="code-regex">.*</span>
            </SearchToggle>
          </div>
        </div>
      </div>
      <div className="code-replace-row" hidden={!replaceOpen}>
        <div className="code-search-box">
          <input
            aria-label="替换内容"
            placeholder="Replace"
            value={replaceText}
            onChange={(event) => setReplaceText(event.target.value)}
          />
          <SearchToggle
            label="保留大小写"
            pressed={preserveCase}
            onClick={() => setPreserveCase(!preserveCase)}
          >
            AB
          </SearchToggle>
        </div>
        <SearchTool
          label="预览全部替换"
          icon="replace-all"
          disabled={!occurrenceCount || searching || replacementBusy || results?.truncated}
          onClick={() => void previewReplacement()}
        />
      </div>
      <div className="code-search-details-toggle">
        <button
          aria-label="搜索详细条件"
          title="包含与排除文件"
          aria-expanded={detailsOpen}
          onClick={() => setDetailsOpen(!detailsOpen)}
        >
          <Icon name="more" size={17} />
        </button>
      </div>
      <div className="code-search-details" hidden={!detailsOpen}>
        <label>
          包含文件
          <input
            aria-label="搜索路径包含"
            placeholder="例如 src/ 或 *.json"
            value={include}
            onChange={(event) => setInclude(event.target.value)}
          />
        </label>
        <label>
          排除文件
          <input
            aria-label="搜索路径排除"
            placeholder="例如 expected/, *.lock"
            value={exclude}
            onChange={(event) => setExclude(event.target.value)}
          />
        </label>
        {regex && <p>正则替换支持 $1、$&#123;name&#125; 和 \n。</p>}
      </div>
    </div>
  );
  const notices = (
    <>
      {results?.matches.length === 0 && !searching && <p className="core-hint">没有匹配结果。</p>}
      {results && (results.truncated || results.skipped > 0) && (
        <p className="core-hint">
          {results.truncated ? "结果达到上限，请缩小范围。" : ""}
          {results.skipped ? `已跳过 ${results.skipped} 个大文件或不可读文本。` : ""}
        </p>
      )}
      {dirty && <p className="core-hint">未保存的编辑不参与磁盘搜索。</p>}
    </>
  );
  return {
    header,
    sidebar: (
      <div className="code-search-panel">
        {controls}
        <div className="code-search-summary" aria-live="polite">
          <span>{status}</span>
          {!!occurrenceCount && !searching && (
            <>
              <span aria-hidden="true">·</span>
              <button onClick={onOpenEditor}>在编辑器中打开</button>
            </>
          )}
        </div>
        {error && (
          <p id="code-search-error" className="code-search-error" role="alert">
            {error}
          </p>
        )}
        {replaceError && !replacementPreview && (
          <p className="code-search-error" role="alert">
            {replaceError}
          </p>
        )}
        {resultTree}
        {!error && notices}
      </div>
    ),
    editor: (
      <section className="code-search-editor" aria-label="搜索结果编辑器">
        <div className="core-document-header">
          <span>
            搜索：{query || "（空）"} · {status}
          </span>
          <div>
            <button onClick={() => setRun((value) => value + 1)}>刷新</button>
            <button onClick={reset}>新建搜索</button>
          </div>
        </div>
        <div className="code-search-editor-results">
          {error && (
            <p className="code-search-error" role="alert">
              {error}
            </p>
          )}
          {replaceError && !replacementPreview && (
            <p className="code-search-error" role="alert">
              {replaceError}
            </p>
          )}
          {resultTree}
          {notices}
        </div>
      </section>
    ),
    dialog: replacementPreview ? (
      <ReplacementDialog
        preview={replacementPreview.result}
        request={replacementPreview.request}
        busy={replacementBusy}
        error={replaceError}
        onClose={() => setReplacementPreview(undefined)}
        onApply={() => void applyReplacement()}
      />
    ) : null,
  };
}
