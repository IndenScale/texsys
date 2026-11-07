import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ActivityBar, WorkbenchLayout } from "./atomstudio/Layout";
import { Empty, Icon } from "./ui";
import type { FilePreview, StudioLayout, StudioState, WorkbenchProps } from "./sdk";
import {
  sourceOffset,
  visibleFileTree,
  type FileListing,
  type GitDiff,
  type GitEntry,
  type GitStatus,
  type SearchMatch,
} from "./workspace-model";
import { visitDocument, stepDocument, type DocumentHistory } from "./navigation";
import { useCodeSearch } from "./CodeSearch";
import { ExplorerTree } from "./ExplorerTree";
import { FileIcon } from "./FileIcon";
import { ExplorerMenu, type ExplorerAction } from "./ExplorerMenu";
import "./workspace.css";
import { SourceControl } from "./SourceControl";

type Activity = "explorer" | "search" | "scm";
type Document = {
  path: string;
  saved: string;
  content: string;
  version: string;
  reason?: string;
  loading?: boolean;
  error?: string;
  location?: SearchMatch;
  position?: number;
};
type FileRequest = {
  path: string;
  token: number;
  line?: number;
  column?: number;
  end_column?: number;
};
type Core = {
  activity: Activity | null;
  select: (id: Activity | null) => void;
  sidebar: ReactNode;
  editor: ReactNode;
  tabs: ReactNode;
  inspector: ReactNode;
  documentActive: boolean;
  activateDomain: () => void;
  hasDomain: boolean;
  changeCount: number;
};
const CoreContext = createContext<Core | null>(null);
export const useCoreWorkspace = () => useContext(CoreContext);
const emptyListing: FileListing = { files: [], directories: [], truncated: false };
const coreItems = [
  { id: "explorer", label: "文件资源管理器", icon: <Icon name="files" size={19} /> },
  { id: "search", label: "搜索", icon: <Icon name="search" size={19} /> },
  { id: "scm", label: "源代码管理", icon: <Icon name="branch" size={19} /> },
];
function Tool({
  label,
  icon,
  onClick,
  disabled,
}: {
  label: string;
  icon: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      className="core-tool"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
    >
      <Icon name={icon} size={15} />
    </button>
  );
}

function DialogLayer({ children, cancel }: { children: ReactNode; cancel: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const cancelRef = useRef(cancel);
  cancelRef.current = cancel;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input,textarea,button")?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        cancelRef.current();
      }
      if (event.key === "Tab") {
        const nodes = Array.from(
          ref.current?.querySelectorAll<HTMLElement>("input,button:not(:disabled),textarea") ?? [],
        );
        if (!nodes.length) return;
        const first = nodes[0],
          last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => {
      document.removeEventListener("keydown", handler);
      previous?.focus();
    };
  }, []);
  return <div ref={ref}>{children}</div>;
}

export function CoreWorkspaceProvider({
  state,
  api,
  refresh,
  notify,
  changeLayout,
  fileRequest,
  activityRequest,
  navigationRequest,
  onNavigationState,
  hasDomain,
  children,
}: {
  state: StudioState;
  api: WorkbenchProps["api"];
  refresh: WorkbenchProps["refresh"];
  notify: WorkbenchProps["notify"];
  changeLayout: WorkbenchProps["changeLayout"];
  fileRequest?: FileRequest;
  activityRequest?: { id: Activity; token: number };
  navigationRequest?: WorkbenchProps["navigationRequest"];
  onNavigationState?: (next: { active: boolean; canBack: boolean; canForward: boolean }) => void;
  hasDomain: boolean;
  children: ReactNode;
}) {
  const [activity, setActivity] = useState<Activity | null>("explorer");
  const [listing, setListing] = useState<FileListing>(emptyListing),
    [listingError, setListingError] = useState("");
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [expanded, setExpanded] = useState(new Set<string>(["src", "assets"]));
  const [explorerMenu, setExplorerMenu] = useState<{ x: number; y: number; path: string }>();
  const [rootOpen, setRootOpen] = useState(true),
    [selected, setSelected] = useState("");
  const [documents, setDocuments] = useState<Document[]>([]),
    [activePath, setActivePath] = useState<string | null>(null);
  const [documentActive, setDocumentActive] = useState(true);
  const [searchEditorActive, setSearchEditorActive] = useState(false);
  const [searchDocumentOpen, setSearchDocumentOpen] = useState(false);
  const [history, setHistory] = useState<DocumentHistory>({ entries: [], index: -1 });
  const [busy, setBusy] = useState(false);
  const [searchGeneration, setSearchGeneration] = useState(0);
  const [gitScope, setGitScope] = useState("project");
  const [gitStatus, setGitStatus] = useState<GitStatus>(),
    [gitError, setGitError] = useState("");
  const [message, setMessage] = useState(""),
    [diff, setDiff] = useState<GitDiff>(),
    [diffActive, setDiffActive] = useState(false);
  const [diffLoading, setDiffLoading] = useState(false),
    [diffError, setDiffError] = useState("");
  const diffSequence = useRef(0);
  const [form, setForm] = useState<{
    action: "create" | "mkdir" | "rename" | "delete";
    path: string;
    value: string;
  }>();
  const [formError, setFormError] = useState("");
  const [closePath, setClosePath] = useState<string>();
  const [diskComparison, setDiskComparison] = useState<{ path: string; file: FilePreview }>();
  const editor = useRef<HTMLTextAreaElement>(null),
    gutter = useRef<HTMLDivElement>(null),
    searchInput = useRef<HTMLInputElement>(null);
  const documentsRef = useRef(documents);
  documentsRef.current = documents;
  const active = documents.find((item) => item.path === activePath);
  const dirty = documents.some((item) => item.content !== item.saved);
  const select = useCallback(
    (id: Activity | null) => {
      setActivity(id);
      changeLayout({ sidebarOpen: true });
    },
    [changeLayout],
  );
  const activateDomain = useCallback(() => {
    setDocumentActive(false);
    setSearchEditorActive(false);
    setDiffActive(false);
  }, []);
  const readFiles = useCallback(async () => {
    setLoadingFiles(true);
    try {
      setListing(await api<FileListing>("/api/files"));
      setListingError("");
    } catch (error) {
      setListingError(String(error));
    } finally {
      setLoadingFiles(false);
    }
  }, [api]);
  const gitReadSequence = useRef(0);
  const readGit = useCallback(async () => {
    const sequence = ++gitReadSequence.current;
    try {
      const next = await api<GitStatus>(`/api/scm?scope=${gitScope}`);
      if (sequence !== gitReadSequence.current) return;
      setGitStatus(next);
      setGitError("");
    } catch (error) {
      if (sequence === gitReadSequence.current) setGitError(String(error));
    }
  }, [api, gitScope]);
  const reload = async () => {
    await Promise.all([readFiles(), readGit(), refresh()]);
    setSearchGeneration((value) => value + 1);
  };
  useEffect(() => {
    void readFiles();
    void readGit();
  }, [readFiles, readGit]);
  useEffect(() => {
    const focus = () => {
      void readFiles();
      void readGit();
    };
    window.addEventListener("focus", focus);
    const timer = setInterval(() => {
      if (activity === "scm" && !busy) void readGit();
    }, 5000);
    return () => {
      window.removeEventListener("focus", focus);
      clearInterval(timer);
    };
  }, [readFiles, readGit, activity, busy]);
  useEffect(() => {
    void readFiles();
  }, [state.project.revision, readFiles]);
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);
  const openFile = async (path: string, location?: FileRequest, recordHistory = true) => {
    if (recordHistory) setHistory((previous) => visitDocument(previous, path));
    setSearchEditorActive(false);
    setRootOpen(true);
    setSelected(path);
    setActivePath(path);
    setDocumentActive(true);
    setDiffActive(false);
    const parents = path.split("/");
    parents.pop();
    setExpanded((previous) => {
      const next = new Set(previous);
      while (parents.length) {
        next.add(parents.join("/"));
        parents.pop();
      }
      return next;
    });
    const target = location?.line
      ? {
          path,
          line: location.line,
          column: location.column ?? 1,
          end_column: location.end_column ?? location.column ?? 1,
          text: "",
          offset: 0,
          length: 0,
        }
      : undefined;
    const existing = documentsRef.current.find((item) => item.path === path);
    if (existing && existing.content !== existing.saved) {
      if (target) notify("文件有未保存的编辑，已打开文件；请保存后再按磁盘搜索结果定位。");
      return;
    }
    setDocuments((previous) =>
      previous.some((item) => item.path === path)
        ? previous
        : [
            ...previous,
            { path, saved: "", content: "", version: "", loading: true, location: target },
          ],
    );
    try {
      const file = await api<FilePreview>(`/api/file?path=${encodeURIComponent(path)}`);
      setDocuments((previous) =>
        previous.map((item) =>
          item.path === path && item.content === item.saved
            ? {
                ...item,
                location: target,
                error: undefined,
                saved: file.content ?? "",
                content: file.content ?? "",
                version: file.version ?? "",
                loading: false,
                reason: file.reason,
              }
            : item,
        ),
      );
    } catch (error) {
      setDocuments((previous) =>
        previous.map((item) =>
          item.path === path ? { ...item, loading: false, error: String(error) } : item,
        ),
      );
    }
  };
  useEffect(() => {
    onNavigationState?.({
      active: documentActive,
      canBack: history.index > 0,
      canForward: history.index < history.entries.length - 1,
    });
  }, [documentActive, history.index, history.entries.length, onNavigationState]);
  useEffect(() => {
    if (
      !documentActive ||
      !navigationRequest ||
      !["navigation.back", "navigation.forward"].includes(navigationRequest.command)
    )
      return;
    const next = stepDocument(history, navigationRequest.command === "navigation.back" ? -1 : 1);
    if (next === history) return;
    setHistory(next);
    void openFile(next.entries[next.index], undefined, false);
  }, [navigationRequest?.token]);
  useEffect(() => {
    if (fileRequest) void openFile(fileRequest.path, fileRequest);
  }, [fileRequest?.token]);
  useEffect(() => {
    if (activityRequest) {
      select(activityRequest.id);
      if (activityRequest.id === "search")
        requestAnimationFrame(() => searchInput.current?.focus());
    }
  }, [activityRequest?.token]);
  useEffect(() => {
    if (!active?.location || active.loading || !documentActive || diffActive || searchEditorActive)
      return;
    const node = editor.current;
    if (!node) return;
    const location = active.location;
    const start = sourceOffset(active.content, location.line, location.column);
    const end = sourceOffset(active.content, location.line, location.end_column);
    node.focus();
    node.setSelectionRange(start, Math.max(start, end));
    node.scrollTop = Math.max(0, (location.line - 4) * 21);
    if (gutter.current) gutter.current.scrollTop = node.scrollTop;
  }, [
    active?.path,
    active?.loading,
    active?.location,
    documentActive,
    diffActive,
    searchEditorActive,
  ]);
  const saveFile = async (path = activePath): Promise<boolean> => {
    const document = documentsRef.current.find((item) => item.path === path);
    if (!document || document.loading || document.reason || document.error) return false;
    setBusy(true);
    try {
      const saved = await api<FilePreview>("/api/file", {
        action: "save",
        path,
        content: document.content,
        version: document.version,
      });
      setDocuments((previous) =>
        previous.map((item) =>
          item.path === path
            ? { ...item, saved: saved.content ?? "", version: saved.version! }
            : item,
        ),
      );
      notify("文件已保存");
      await reload();
      return true;
    } catch (error) {
      notify(String(error), true);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const compareDisk = async (path: string) => {
    try {
      const file = await api<FilePreview>(`/api/file?path=${encodeURIComponent(path)}`);
      if (!file.previewable) throw new Error(file.reason ?? "磁盘版本无法作为文本读取。");
      setDiskComparison({ path, file });
    } catch (error) {
      notify(String(error), true);
    }
  };
  const rereadFile = async (path: string) => {
    try {
      const file = await api<FilePreview>(`/api/file?path=${encodeURIComponent(path)}`);
      setDocuments((previous) =>
        previous.map((item) =>
          item.path === path
            ? {
                ...item,
                saved: file.content ?? "",
                content: file.content ?? "",
                version: file.version ?? "",
                error: undefined,
                reason: file.reason,
                location: undefined,
              }
            : item,
        ),
      );
    } catch (error) {
      notify(String(error), true);
    }
  };
  const closeDocument = (path: string, discard = false) => {
    const document = documentsRef.current.find((item) => item.path === path);
    if (!discard && document && document.saved !== document.content) {
      setClosePath(path);
      return;
    }
    setDocuments((previous) => previous.filter((item) => item.path !== path));
    if (activePath === path)
      setActivePath(documentsRef.current.filter((item) => item.path !== path).at(-1)?.path ?? null);
    setClosePath(undefined);
  };
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key.toLowerCase() === "s" && documentActive && !diffActive && !searchEditorActive) {
        event.preventDefault();
        if (!busy) void saveFile();
      }
      if (event.shiftKey && ["e", "f", "g"].includes(event.key.toLowerCase())) {
        event.preventDefault();
        const id = ({ e: "explorer", f: "search", g: "scm" } as const)[
          event.key.toLowerCase() as "e" | "f" | "g"
        ];
        select(id);
        if (id === "search") requestAnimationFrame(() => searchInput.current?.focus());
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [documentActive, diffActive, searchEditorActive, busy, activePath, documents, select]);
  const submitFileOperation = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form) return;
    if (
      documentsRef.current.some((item) => item.path === form.path && item.content !== item.saved)
    ) {
      setFormError("请先保存或关闭该文件的未保存编辑。");
      return;
    }
    setBusy(true);
    setFormError("");
    try {
      let version;
      if (
        ["rename", "delete"].includes(form.action) &&
        listing.files.some((file) => file.path === form.path)
      ) {
        const file = await api<FilePreview>(`/api/file?path=${encodeURIComponent(form.path)}`);
        if (!file.version) throw new Error("无法读取文件版本，请刷新后重试。");
        version = file.version;
      }
      await api("/api/file", {
        action: form.action,
        path: ["create", "mkdir"].includes(form.action) ? form.value : form.path,
        target: form.value,
        version,
      });
      if (["rename", "delete"].includes(form.action)) closeDocument(form.path, true);
      setForm(undefined);
      setSelected("");
      await reload();
      if (
        form.action === "create" ||
        (form.action === "rename" && !listing.directories.includes(form.path))
      )
        await openFile(form.value);
      if (form.action === "mkdir") {
        const parts = form.value.split("/");
        setExpanded((previous) => {
          const next = new Set(previous);
          while (parts.length) {
            next.add(parts.join("/"));
            parts.pop();
          }
          return next;
        });
        setSelected(form.value);
      }
    } catch (error) {
      setFormError(String(error));
    } finally {
      setBusy(false);
    }
  };
  const scmAction = async (action: string, entry?: GitEntry) => {
    setBusy(true);
    setGitError("");
    try {
      const next = await api<GitStatus>("/api/scm", {
        action,
        path: entry?.path,
        scope: gitScope,
        snapshot: gitStatus?.snapshot,
        message,
      });
      setGitStatus(next);
      if (action === "discard" && entry) {
        const path = gitStatus?.project_prefix
          ? entry.path.startsWith(gitStatus.project_prefix + "/")
            ? entry.path.slice(gitStatus.project_prefix.length + 1)
            : null
          : entry.path;
        setDocuments((previous) => previous.filter((item) => item.path !== path));
      }
      if (action === "commit") {
        setMessage("");
        notify("已创建本地提交");
      }
      // An open diff describes the previous index/worktree; close it after a mutation.
      setDiffActive(false);
      setDiff(undefined);
      diffSequence.current += 1;
      await reload();
    } catch (error) {
      setGitError(String(error));
      await readGit();
    } finally {
      setBusy(false);
    }
  };
  const openDiff = async (entry: GitEntry, staged: boolean) => {
    setSearchEditorActive(false);
    const sequence = ++diffSequence.current;
    setDocumentActive(true);
    setDiffActive(true);
    setDiff(undefined);
    setDiffError("");
    setDiffLoading(true);
    try {
      const result = await api<GitDiff>(
        `/api/scm/diff?${new URLSearchParams({ path: entry.path, staged: String(staged), scope: gitScope })}`,
      );
      if (sequence === diffSequence.current) setDiff(result);
    } catch (error) {
      if (sequence === diffSequence.current) setDiffError(String(error));
    } finally {
      if (sequence === diffSequence.current) setDiffLoading(false);
    }
  };
  const codeSearch = useCodeSearch({
    api,
    revision: state.project.revision,
    generation: searchGeneration,
    inputRef: searchInput,
    onOpenFile: (path, match) => void openFile(path, match ? { ...match, token: 0 } : undefined),
    onOpenEditor: () => {
      setSearchDocumentOpen(true);
      setSearchEditorActive(true);
      setDocumentActive(true);
      setDiffActive(false);
    },
    onChanged: async (paths) => {
      await reload();
      for (const path of paths)
        if (documentsRef.current.some((item) => item.path === path && item.content === item.saved))
          await rereadFile(path);
    },
    hasDirtyFile: (path) =>
      documentsRef.current.some((item) => item.path === path && item.content !== item.saved),
    dirty,
    gitEntries: gitStatus?.entries ?? [],
    notify,
  });
  const tree = visibleFileTree(listing, expanded);
  const startForm = (action: "create" | "mkdir" | "rename" | "delete", path = selected) => {
    const parent = listing.directories.includes(path)
      ? path
      : path.split("/").slice(0, -1).join("/");
    setFormError("");
    setForm({
      action,
      path,
      value: action === "rename" ? path : parent ? parent + "/" : "",
    });
  };
  const invokeExplorerAction = (action: ExplorerAction) => {
    const path = explorerMenu?.path ?? selected;
    setExplorerMenu(undefined);
    if (action === "refresh") void reload();
    else if (action === "collapse") setExpanded(new Set());
    else startForm(action, path);
  };
  useEffect(() => {
    setExplorerMenu(undefined);
  }, [activity]);
  const headings = { explorer: "Explorer", search: "搜索", scm: "源代码管理" };
  const sidebar = (
    <div className="core-sidebar">
      {activity === "search" ? (
        codeSearch.header
      ) : (
        <div className="core-heading">
          <strong>{activity ? headings[activity] : ""}</strong>
          {activity === "explorer" ? (
            <button
              className="core-tool"
              aria-label="Explorer 更多操作"
              title="更多操作"
              aria-haspopup="menu"
              aria-expanded={!!explorerMenu}
              onClick={(event) => {
                if (explorerMenu) setExplorerMenu(undefined);
                else {
                  const rect = event.currentTarget.getBoundingClientRect();
                  setExplorerMenu({ x: rect.right - 188, y: rect.bottom + 3, path: selected });
                }
              }}
            >
              <Icon name="more" size={18} />
            </button>
          ) : (
            <Tool label="刷新资源" icon="refresh" disabled={busy} onClick={() => void reload()} />
          )}
        </div>
      )}
      <section
        className="core-resource"
        hidden={activity !== "explorer"}
        aria-label="文件资源管理器内容"
      >
        <div className="core-project-row" data-expanded={rootOpen}>
          <button
            aria-label={`项目 ${state.project.name}`}
            className="core-project-root"
            title={state.project.name}
            onClick={() => setRootOpen(!rootOpen)}
            aria-expanded={rootOpen}
          >
            <span className="core-tree-twistie" aria-hidden="true">
              <Icon name="chevron" size={12} expanded={rootOpen} />
            </span>
            <strong>{state.project.name}</strong>
          </button>
          <div
            className="core-tools core-project-tools"
            role="toolbar"
            aria-label="项目文件操作"
            hidden={!rootOpen}
          >
            <Tool
              label="新建文件"
              icon="new-file"
              onClick={() => startForm("create")}
              disabled={busy}
            />
            <Tool
              label="新建文件夹"
              icon="new-folder"
              onClick={() => startForm("mkdir")}
              disabled={busy}
            />
            <Tool label="刷新资源" icon="refresh" onClick={() => void reload()} disabled={busy} />
            <Tool label="全部折叠" icon="collapse" onClick={() => setExpanded(new Set())} />
          </div>
        </div>
        {listingError && (
          <p className="core-error" role="alert">
            {listingError}
          </p>
        )}
        {loadingFiles && !listing.files.length && <p className="core-hint">正在读取文件…</p>}
        {rootOpen && (
          <ExplorerTree
            entries={tree}
            expanded={expanded}
            selected={selected}
            dirtyPaths={
              new Set(
                documents
                  .filter((document) => document.content !== document.saved)
                  .map((document) => document.path),
              )
            }
            select={setSelected}
            toggle={(path) =>
              setExpanded((previous) => {
                const next = new Set(previous);
                if (next.has(path)) next.delete(path);
                else next.add(path);
                return next;
              })
            }
            open={(path) => void openFile(path)}
            contextMenu={(path, x, y) => setExplorerMenu({ path, x, y })}
            rename={() => startForm("rename")}
            remove={() => startForm("delete")}
          />
        )}
        {listing.truncated && <p className="core-hint">文件列表达到上限，部分文件未显示。</p>}
        {!loadingFiles && !listing.files.length && !listing.directories.length && (
          <p className="core-hint">项目中尚无文件，可在上方新建。</p>
        )}
      </section>
      <section
        className="core-resource core-search"
        hidden={activity !== "search"}
        aria-label="全文搜索内容"
      >
        {codeSearch.sidebar}
      </section>
      <section
        className="core-resource core-scm"
        hidden={activity !== "scm"}
        aria-label="源代码管理内容"
      >
        {gitError && (
          <p className="core-error" role="alert">
            {gitError}
          </p>
        )}
        {!gitStatus ? (
          <p className="core-hint">正在读取 Git 状态…</p>
        ) : !gitStatus.available ? (
          <div className="core-scm-empty">
            <Icon name="branch" size={26} />
            <p>{gitStatus.reason}</p>
            <button className="core-primary" disabled={busy} onClick={() => void scmAction("init")}>
              初始化 Git 仓库
            </button>
          </div>
        ) : (
          <>
            <SourceControl
              scope={gitScope}
              changeScope={(scope) => {
                gitReadSequence.current += 1;
                setGitStatus(undefined);
                setGitScope(scope);
                setDiffActive(false);
              }}
              status={gitStatus}
              busy={busy}
              dirty={dirty}
              message={message}
              setMessage={setMessage}
              action={scmAction}
              openDiff={openDiff}
              openFile={openFile}
              api={api}
              refresh={readGit}
              openCommit={(result) => {
                diffSequence.current += 1;
                setDiff(result);
                setDiffActive(true);
                setDocumentActive(true);
                setDiffLoading(false);
                setDiffError("");
              }}
            />
          </>
        )}
      </section>
    </div>
  );
  const dialogs = (
    <>
      {form && (
        <div className="core-dialog-backdrop">
          <form
            className="core-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={
              { create: "新建文件", mkdir: "新建文件夹", rename: "重命名", delete: "删除文件" }[
                form.action
              ]
            }
            onSubmit={(event) => void submitFileOperation(event)}
          >
            <strong>
              {
                { create: "新建文件", mkdir: "新建文件夹", rename: "重命名", delete: "删除文件" }[
                  form.action
                ]
              }
            </strong>
            {form.action === "delete" ? (
              <p>删除“{form.path}”？此操作会移除磁盘上的文件或空文件夹。</p>
            ) : (
              <label>
                项目相对路径
                <input
                  autoFocus
                  aria-label="文件路径"
                  value={form.value}
                  onChange={(event) => setForm({ ...form, value: event.target.value })}
                />
              </label>
            )}
            <p className="core-error" role="alert">
              {formError}
            </p>
            <div className="core-dialog-actions">
              <button type="button" onClick={() => setForm(undefined)} disabled={busy}>
                取消
              </button>
              <button
                className="core-primary"
                disabled={busy || (form.action !== "delete" && !form.value.trim())}
              >
                {form.action === "delete" ? "删除" : "确定"}
              </button>
            </div>
          </form>
        </div>
      )}
      {closePath && (
        <div className="core-dialog-backdrop">
          <div className="core-dialog" role="dialog" aria-modal="true" aria-label="保存文件更改">
            <strong>保存文件更改？</strong>
            <p>{closePath} 有未保存的编辑。</p>
            <div className="core-dialog-actions">
              <button onClick={() => setClosePath(undefined)}>取消</button>
              <button onClick={() => closeDocument(closePath, true)}>放弃更改</button>
              <button
                className="core-primary"
                disabled={busy}
                onClick={async () => {
                  if (await saveFile(closePath)) closeDocument(closePath, true);
                }}
              >
                保存并关闭
              </button>
            </div>
          </div>
        </div>
      )}
      {diskComparison && (
        <div className="core-dialog-backdrop">
          <div
            className="core-dialog core-compare-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="比较磁盘版本"
          >
            <strong>比较磁盘版本 · {diskComparison.path}</strong>
            <div className="core-compare-columns">
              <label>
                当前编辑
                <textarea
                  readOnly
                  aria-label="比较当前编辑"
                  value={documents.find((item) => item.path === diskComparison.path)?.content ?? ""}
                />
              </label>
              <label>
                磁盘内容
                <textarea
                  readOnly
                  aria-label="比较磁盘内容"
                  value={diskComparison.file.content ?? ""}
                />
              </label>
            </div>
            <p>
              可以采用磁盘内容，或保留当前编辑并更新保存基准。保留编辑后，仍需点击保存才会写入文件。
            </p>
            <div className="core-dialog-actions">
              <button onClick={() => setDiskComparison(undefined)}>关闭</button>
              <button
                onClick={() => {
                  setDocuments((previous) =>
                    previous.map((item) =>
                      item.path === diskComparison.path
                        ? {
                            ...item,
                            content: diskComparison.file.content ?? "",
                            saved: diskComparison.file.content ?? "",
                            version: diskComparison.file.version!,
                            location: undefined,
                          }
                        : item,
                    ),
                  );
                  setDiskComparison(undefined);
                }}
              >
                采用磁盘内容
              </button>
              <button
                className="core-primary"
                onClick={() => {
                  setDocuments((previous) =>
                    previous.map((item) =>
                      item.path === diskComparison.path
                        ? {
                            ...item,
                            saved: diskComparison.file.content ?? "",
                            version: diskComparison.file.version!,
                            location: undefined,
                          }
                        : item,
                    ),
                  );
                  setDiskComparison(undefined);
                }}
              >
                保留编辑并更新保存基准
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
  const tabs = (
    <div
      className="core-file-tabs"
      role="tablist"
      aria-label="文件文档"
      onKeyDown={(event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        const nodes = Array.from(
          event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
        );
        const index = nodes.indexOf(event.target as HTMLButtonElement);
        if (index < 0) return;
        event.preventDefault();
        const next =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? nodes.length - 1
              : (index + (event.key === "ArrowRight" ? 1 : nodes.length - 1)) % nodes.length;
        nodes[next]?.focus();
        nodes[next]?.click();
      }}
    >
      {documents.map((document) => (
        <div
          className={`core-file-tab ${documentActive && !diffActive && !searchEditorActive && activePath === document.path ? "active" : ""}`}
          key={document.path}
        >
          <button
            role="tab"
            tabIndex={activePath === document.path ? 0 : -1}
            aria-selected={
              documentActive && !diffActive && !searchEditorActive && activePath === document.path
            }
            title={document.path}
            onClick={() => {
              setSearchEditorActive(false);
              setHistory((previous) => visitDocument(previous, document.path));
              setActivePath(document.path);
              setDocumentActive(true);
              setDiffActive(false);
            }}
          >
            <FileIcon path={document.path} />
            <span>{document.path.split("/").pop()}</span>
            {document.content !== document.saved && <span className="core-dirty" title="未保存" />}
          </button>
          <Tool
            label={`关闭 ${document.path}`}
            icon="close"
            onClick={() => closeDocument(document.path)}
          />
        </div>
      ))}
      {searchDocumentOpen && (
        <div className={`core-file-tab ${documentActive && searchEditorActive ? "active" : ""}`}>
          <button
            role="tab"
            aria-selected={documentActive && searchEditorActive}
            onClick={() => {
              setSearchEditorActive(true);
              setDocumentActive(true);
              setDiffActive(false);
            }}
          >
            <Icon name="search" size={14} />
            <span>搜索结果</span>
          </button>
          <Tool
            label="关闭搜索结果"
            icon="close"
            onClick={() => {
              setSearchDocumentOpen(false);
              setSearchEditorActive(false);
            }}
          />
        </div>
      )}
      {diffActive && (
        <div className="core-file-tab active">
          <span>差异 · {diff?.path.split("/").pop() ?? "读取中"}</span>
          <Tool label="关闭差异" icon="close" onClick={() => setDiffActive(false)} />
        </div>
      )}
    </div>
  );
  const editorContent = searchEditorActive ? (
    codeSearch.editor
  ) : diffActive ? (
    <section className="core-diff-document" aria-label="Git 差异">
      <div className="core-document-header">
        <span>
          {diff?.path}{" "}
          {diff ? `· ${diff.title ?? (diff.staged ? "已暂存 ↔ HEAD" : "工作区 ↔ 暂存区")}` : ""}
        </span>
        {diff && !diff.historical && gitScope === "project" && (
          <button onClick={() => void openFile(diff.path)}>打开文件</button>
        )}
      </div>
      {diffLoading ? (
        <p className="core-hint">正在读取差异…</p>
      ) : diffError ? (
        <p className="core-error">{diffError}</p>
      ) : (
        <>
          <pre>
            {diff?.content
              ? diff.content.split("\n").map((line, index) => (
                  <div
                    key={index}
                    className={
                      line.startsWith("+") && !line.startsWith("+++")
                        ? "added"
                        : line.startsWith("-") && !line.startsWith("---")
                          ? "removed"
                          : line.startsWith("@@")
                            ? "hunk"
                            : ""
                    }
                  >
                    {line || " "}
                  </div>
                ))
              : "没有文本差异。文件可能仅有权限变更。"}
          </pre>
          {diff?.truncated && <p className="core-hint">差异超过 1 MiB，显示已截断。</p>}
        </>
      )}
    </section>
  ) : !active ? (
    <div className="core-welcome">
      <Icon name="files" size={38} />
      <h2>打开文件，开始编辑</h2>
      <p>从文件资源管理器选择文件，或使用 ⌘ / Ctrl + P 快速打开。</p>
      {hasDomain && (
        <button className="core-primary" onClick={activateDomain}>
          打开专业预览
        </button>
      )}
    </div>
  ) : active.loading ? (
    <Empty title="正在打开文件">{active.path}</Empty>
  ) : active.error || active.reason ? (
    <Empty title={active.error ? "文件读取失败" : "暂不支持编辑此文件"}>
      {active.error ?? active.reason}
      <button
        onClick={() => {
          void rereadFile(active.path);
        }}
      >
        重新读取
      </button>
    </Empty>
  ) : (
    <section className="core-text-document" aria-label={`编辑 ${active.path}`}>
      <div className="core-document-header">
        <nav className="core-breadcrumb" aria-label="文件路径" title={active.path}>
          {active.path.split("/").map((part, index, parts) => (
            <React.Fragment key={index}>
              {index > 0 && (
                <span className="core-breadcrumb-separator">
                  <Icon name="chevron" size={13} />
                </span>
              )}
              {index < parts.length - 1 ? (
                <button
                  onClick={() => {
                    const path = parts.slice(0, index + 1).join("/");
                    setRootOpen(true);
                    setSelected(path);
                    setExpanded(
                      (previous) =>
                        new Set([
                          ...previous,
                          ...parts
                            .slice(0, index + 1)
                            .map((_, end) => parts.slice(0, end + 1).join("/")),
                        ]),
                    );
                    select("explorer");
                  }}
                >
                  {part}
                </button>
              ) : (
                <span className="core-breadcrumb-file">
                  <FileIcon path={active.path} />
                  {part}
                </span>
              )}
            </React.Fragment>
          ))}
        </nav>
        <div>
          <span className="core-save-state">
            {active.content !== active.saved ? "未保存" : "已保存"}
          </span>
          <Tool
            label="与磁盘内容比较"
            icon="compare"
            disabled={busy}
            onClick={() => void compareDisk(active.path)}
          />
          <Tool
            label="重新读取文件"
            icon="refresh"
            disabled={busy || active.content !== active.saved}
            onClick={() => {
              void rereadFile(active.path);
            }}
          />
          <button
            className="core-save-button"
            disabled={busy || active.content === active.saved}
            onClick={() => void saveFile()}
          >
            保存
          </button>
        </div>
      </div>
      <div className="core-code-editor">
        <div className="core-code-gutter" ref={gutter} aria-hidden="true">
          {Array.from({ length: active.content.split("\n").length }, (_, index) => index + 1).join(
            "\n",
          )}
        </div>
        <textarea
          key={active.path}
          ref={editor}
          aria-label="文件内容"
          value={active.content}
          spellCheck={false}
          wrap="off"
          onScroll={(event) => {
            if (gutter.current) gutter.current.scrollTop = event.currentTarget.scrollTop;
          }}
          onSelect={(event) => {
            const position = event.currentTarget.selectionStart;
            setDocuments((previous) =>
              previous.map((item) => (item.path === active.path ? { ...item, position } : item)),
            );
          }}
          onChange={(event) => {
            const value = event.target.value;
            setDocuments((previous) =>
              previous.map((item) =>
                item.path === active.path ? { ...item, content: value, location: undefined } : item,
              ),
            );
          }}
          onKeyDown={(event) => {
            if (event.key === "Tab") {
              event.preventDefault();
              const target = event.currentTarget,
                start = target.selectionStart,
                end = target.selectionEnd;
              const content = active.content.slice(0, start) + "  " + active.content.slice(end);
              setDocuments((previous) =>
                previous.map((item) =>
                  item.path === active.path ? { ...item, content, location: undefined } : item,
                ),
              );
              requestAnimationFrame(() => target.setSelectionRange(start + 2, start + 2));
            }
          }}
        />
      </div>
      <div className="core-editor-status">
        <span>UTF-8 · {active.path.split(".").pop()?.toUpperCase()}</span>
        <span>
          行 {active.content.slice(0, active.position ?? 0).split("\n").length} ·{" "}
          {active.content.split("\n").length} 行
        </span>
      </div>
    </section>
  );
  const inspector = (
    <section className="core-file-properties">
      <div className="core-heading">
        <strong>文件属性</strong>
      </div>
      {active && !diffActive && !searchEditorActive ? (
        <dl>
          <dt>路径</dt>
          <dd>{active.path}</dd>
          <dt>编码</dt>
          <dd>UTF-8</dd>
          <dt>状态</dt>
          <dd>
            {active.loading
              ? "读取中"
              : active.reason
                ? "不可编辑"
                : active.content !== active.saved
                  ? "未保存"
                  : "已保存"}
          </dd>
          <dt>行数</dt>
          <dd>{active.content.split("\n").length}</dd>
        </dl>
      ) : (
        <p className="core-hint">选择文件后查看属性。</p>
      )}
    </section>
  );
  const value: Core = {
    activity,
    select,
    sidebar,
    editor: editorContent,
    tabs,
    inspector,
    documentActive,
    activateDomain,
    hasDomain,
    changeCount: gitStatus?.entries.length ?? 0,
  };
  return (
    <CoreContext.Provider value={value}>
      {children}
      {codeSearch.dialog}
      {explorerMenu && (
        <ExplorerMenu
          x={explorerMenu.x}
          y={explorerMenu.y}
          hasSelection={!!explorerMenu.path}
          busy={busy}
          close={() => setExplorerMenu(undefined)}
          invoke={invokeExplorerAction}
        />
      )}
      {(form || closePath || diskComparison) && (
        <DialogLayer
          cancel={() => {
            if (!busy) {
              setForm(undefined);
              setClosePath(undefined);
              setDiskComparison(undefined);
            }
          }}
        >
          {dialogs}
        </DialogLayer>
      )}
    </CoreContext.Provider>
  );
}

/** Host-owned composition; professional plugins contribute resources and preview surfaces. */
export function WorkbenchSurface({
  layout,
  change,
  activityItems = [],
  activeActivity = "",
  onActivity,
  sidebar,
  inspector,
  bottom,
  tabs,
  children,
}: {
  layout: StudioLayout;
  change: WorkbenchProps["changeLayout"];
  activityItems?: { id: string; label: string; icon: ReactNode; count?: number }[];
  activeActivity?: string;
  onActivity?: (id: string) => void;
  sidebar?: ReactNode;
  inspector?: ReactNode;
  bottom?: ReactNode;
  tabs?: ReactNode;
  children?: ReactNode;
}) {
  const core = useCoreWorkspace();
  if (!core) throw new Error("WorkbenchSurface requires the Studio host.");
  const items = [
    ...coreItems.map((item) => ({
      ...item,
      count: item.id === "scm" ? core.changeCount : undefined,
    })),
    ...activityItems.map((item, index) => ({ ...item, extensionStart: index === 0 })),
  ];
  return (
    <WorkbenchLayout
      layout={layout}
      change={change}
      activity={
        <ActivityBar
          active={core.activity ?? activeActivity}
          items={items}
          select={(id) => {
            if (["explorer", "search", "scm"].includes(id)) core.select(id as Activity);
            else {
              core.select(null);
              onActivity?.(id);
            }
          }}
        />
      }
      sidebar={
        <>
          <div className="core-sidebar-slot" hidden={!core.activity}>
            {core.sidebar}
          </div>
          <div className="core-sidebar-slot" hidden={!!core.activity}>
            {sidebar}
          </div>
        </>
      }
      inspector={
        <>
          <div className="core-inspector-slot" hidden={!core.documentActive}>
            {core.inspector}
          </div>
          <div className="core-inspector-slot" hidden={core.documentActive}>
            {inspector}
          </div>
        </>
      }
      bottom={bottom}
    >
      <div className="core-editor-surface">
        <div className="core-document-tabs">
          {core.tabs}
          {tabs && (
            <div
              className={`core-domain-tabs ${core.documentActive ? "inactive" : ""}`}
              onClick={(event) => {
                if ((event.target as HTMLElement).closest('[role="tab"]')) core.activateDomain();
              }}
            >
              {tabs}
            </div>
          )}
        </div>
        <div className="core-surface-content">
          <div className="core-document-content" hidden={!core.documentActive}>
            {core.editor}
          </div>
          <div className="core-domain-content" hidden={core.documentActive}>
            {children}
          </div>
        </div>
      </div>
    </WorkbenchLayout>
  );
}
