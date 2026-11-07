import { t } from "./i18n";
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
  gitPathInProject,
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
import { ScmMenu, SourceControl } from "./SourceControl";
import { SourceEditor, type SourceModels } from "./SourceEditor";
import { Settings } from "./Settings";

type Activity = "explorer" | "search" | "scm";
function isCoreActivity(id: string): id is Activity {
  return id === "explorer" || id === "search" || id === "scm";
}
type Document = {
  path: string;
  saved: string;
  content: string;
  version: string;
  reason?: string;
  loading?: boolean;
  error?: string;
  location?: SearchMatch;
  cursorLine?: number;
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
  select: (id: Activity) => void;
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
const coreSidebarTitles: Record<Activity, string> = {
  explorer: "文件资源管理器",
  search: "搜索",
  scm: "源代码管理",
};
function Tool({
  label,
  icon,
  onClick,
  disabled,
}: {
  label: string;
  icon: string;
  onClick: React.MouseEventHandler<HTMLButtonElement>;
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
  sidebarActivity = "explorer",
  fileRequest,
  activityRequest,
  settingsRequest,
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
  sidebarActivity?: string;
  fileRequest?: FileRequest;
  activityRequest?: { id: Activity; token: number };
  settingsRequest?: number;
  navigationRequest?: WorkbenchProps["navigationRequest"];
  onNavigationState?: (next: { active: boolean; canBack: boolean; canForward: boolean }) => void;
  hasDomain: boolean;
  children: ReactNode;
}) {
  const coreActivity = isCoreActivity(sidebarActivity) ? sidebarActivity : null;
  const activity = coreActivity ?? "explorer";
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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsActive, setSettingsActive] = useState(false);
  const [searchEditorActive, setSearchEditorActive] = useState(false);
  const [searchDocumentOpen, setSearchDocumentOpen] = useState(false);
  const [history, setHistory] = useState<DocumentHistory>({ entries: [], index: -1 });
  const [busy, setBusy] = useState(false);
  const [searchGeneration, setSearchGeneration] = useState(0);
  const [gitScope, setGitScope] = useState<"project" | "repository">("repository");
  const [gitRefreshGeneration, setGitRefreshGeneration] = useState(0);
  const [gitGraphExpanded, setGitGraphExpanded] = useState<boolean>();
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
  const searchInput = useRef<HTMLInputElement>(null);
  const [sourceModels] = useState<SourceModels>(() => new Map());
  useEffect(() => {
    const paths = new Set(documents.map(({ path }) => path));
    for (const [path, { model }] of sourceModels) {
      if (!paths.has(path)) {
        model.dispose();
        sourceModels.delete(path);
      }
    }
  }, [documents, sourceModels]);
  useEffect(
    () => () => {
      for (const { model } of sourceModels.values()) model.dispose();
      sourceModels.clear();
    },
    [sourceModels],
  );
  const documentsRef = useRef(documents);
  documentsRef.current = documents;
  const active = documents.find((item) => item.path === activePath);
  const dirty = documents.some((item) => item.content !== item.saved);
  const select = useCallback(
    (id: Activity) => changeLayout({ sidebarOpen: true, sidebarActivity: id }),
    [changeLayout],
  );
  const activateDomain = useCallback(() => {
    setSettingsActive(false);
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
    setSettingsActive(false);
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
      if (target) notify(t("文件有未保存的编辑，已打开文件；请保存后再按磁盘搜索结果定位。"));
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
      active: documentActive || settingsActive,
      canBack: !settingsActive && history.index > 0,
      canForward: !settingsActive && history.index < history.entries.length - 1,
    });
  }, [documentActive, settingsActive, history.index, history.entries.length, onNavigationState]);
  useEffect(() => {
    if (
      !documentActive ||
      settingsActive ||
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
    if (!settingsRequest) return;
    setSettingsOpen(true);
    setSettingsActive(true);
  }, [settingsRequest]);
  useEffect(() => {
    if (activityRequest) {
      select(activityRequest.id);
      if (activityRequest.id === "search")
        requestAnimationFrame(() => searchInput.current?.focus());
    }
  }, [activityRequest?.token]);
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
      notify(t("文件已保存"));
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
      if (!file.previewable) throw new Error(file.reason ?? t("磁盘版本无法作为文本读取。"));
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
      setFormError(t("请先保存或关闭该文件的未保存编辑。"));
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
        if (!file.version) throw new Error(t("无法读取文件版本，请刷新后重试。"));
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
        const path = gitPathInProject(entry.path, next.project_prefix);
        if (path !== undefined) {
          setDocuments((previous) => previous.filter((item) => item.path !== path));
        }
      }
      if (action === "commit") {
        setMessage("");
        notify(t("已创建本地提交"));
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
  const openScmDiff = async (url: string) => {
    setSettingsActive(false);
    setSearchEditorActive(false);
    const sequence = ++diffSequence.current;
    setDocumentActive(true);
    setDiffActive(true);
    setDiff(undefined);
    setDiffError("");
    setDiffLoading(true);
    try {
      const result = await api<GitDiff>(url);
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
      setSettingsActive(false);
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
    gitEntries:
      gitStatus?.entries.flatMap((entry) => {
        const path = gitPathInProject(entry.path, gitStatus.project_prefix);
        return path === undefined ? [] : [{ ...entry, path }];
      }) ?? [],
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
  const refreshScm = () => {
    void readGit();
    setGitRefreshGeneration((value) => value + 1);
  };
  const changeScmScope = (scope: "project" | "repository") => {
    if (scope === gitScope) return;
    gitReadSequence.current += 1;
    diffSequence.current += 1;
    setGitStatus(undefined);
    setGitScope(scope);
    setDiffActive(false);
  };
  const sidebarActions =
    activity === "search" ? (
      codeSearch.actions
    ) : activity === "explorer" ? (
      <button
        className="core-tool"
        aria-label={t("资源管理器更多操作")}
        title={t("更多操作")}
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
    ) : activity === "scm" ? (
      <ScmMenu
        label={t("源码管理范围")}
        title={`${t("管理范围")} · ${t(gitScope === "project" ? "当前项目" : "整个仓库")}`}
        items={[
          {
            label: t("当前项目"),
            checked: gitScope === "project",
            reason: gitScope === "project" ? gitStatus?.scope : undefined,
            run: busy || !gitStatus?.available ? undefined : () => changeScmScope("project"),
          },
          {
            label: t("整个仓库"),
            checked: gitScope === "repository",
            reason: gitStatus?.repository,
            run: busy || !gitStatus?.available ? undefined : () => changeScmScope("repository"),
          },
          ...(gitStatus?.available
            ? []
            : [{ label: t("刷新源代码管理"), run: busy ? undefined : refreshScm }]),
        ]}
      />
    ) : (
      <Tool label={t("刷新资源")} icon="refresh" disabled={busy} onClick={() => void reload()} />
    );
  const sidebar = (
    <div className="core-sidebar" data-activity={activity}>
      <div className="navigator-heading">
        <strong title={t(coreSidebarTitles[activity])}>{t(coreSidebarTitles[activity])}</strong>
        <div className="core-tools">{sidebarActions}</div>
      </div>
      <section
        className="core-resource"
        hidden={activity !== "explorer"}
        aria-label={t("文件资源管理器内容")}
      >
        <div className="core-project-row" data-expanded={rootOpen}>
          <button
            aria-label={t("项目 {0}", { "0": state.project.name })}
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
            aria-label={t("项目文件操作")}
            hidden={!rootOpen}
          >
            <Tool
              label={t("新建文件")}
              icon="new-file"
              onClick={() => startForm("create")}
              disabled={busy}
            />
            <Tool
              label={t("新建文件夹")}
              icon="new-folder"
              onClick={() => startForm("mkdir")}
              disabled={busy}
            />
            <Tool
              label={t("刷新资源")}
              icon="refresh"
              onClick={() => void reload()}
              disabled={busy}
            />
            <Tool label={t("全部折叠")} icon="collapse" onClick={() => setExpanded(new Set())} />
          </div>
        </div>
        {listingError && (
          <p className="core-error" role="alert">
            {listingError}
          </p>
        )}
        {loadingFiles && !listing.files.length && <p className="core-hint">{t("正在读取文件…")}</p>}
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
        {listing.truncated && (
          <p className="core-hint">{t("文件列表达到上限，部分文件未显示。")}</p>
        )}
        {!loadingFiles && !listing.files.length && !listing.directories.length && (
          <p className="core-hint">{t("项目中尚无文件，可在上方新建。")}</p>
        )}
      </section>
      <section
        className="core-resource core-search"
        hidden={activity !== "search"}
        aria-label={t("全文搜索内容")}
      >
        {codeSearch.sidebar}
      </section>
      <section
        className="core-resource core-scm"
        hidden={activity !== "scm"}
        aria-label={t("源代码管理内容")}
      >
        {gitError && (
          <p className="core-error" role="alert">
            {gitError}
          </p>
        )}
        {!gitStatus ? (
          <p className="core-hint">{t("正在读取 Git 状态…")}</p>
        ) : !gitStatus.available ? (
          <div className="core-scm-empty">
            <Icon name="branch" size={26} />
            <p>{t(gitStatus.reason)}</p>
            <button className="core-primary" disabled={busy} onClick={() => void scmAction("init")}>
              {t("初始化 Git 仓库")}
            </button>
          </div>
        ) : (
          <>
            <SourceControl
              status={gitStatus}
              active={activity === "scm"}
              activeDiff={diffActive && !settingsActive ? diff : undefined}
              busy={busy}
              dirty={dirty}
              message={message}
              setMessage={setMessage}
              action={scmAction}
              openDiff={(entry, staged) =>
                openScmDiff(
                  `/api/scm/diff?${new URLSearchParams({ path: entry.path, staged: String(staged), scope: gitScope })}`,
                )
              }
              openFile={openFile}
              api={api}
              refreshGeneration={gitRefreshGeneration}
              refresh={refreshScm}
              graphExpanded={gitGraphExpanded ?? gitStatus.entries.length === 0}
              setGraphExpanded={setGitGraphExpanded}
              openCommit={(id) =>
                openScmDiff(`/api/scm/commit?${new URLSearchParams({ id, scope: gitScope })}`)
              }
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
              {
                create: t("新建文件"),
                mkdir: t("新建文件夹"),
                rename: t("重命名"),
                delete: t("删除文件"),
              }[form.action]
            }
            onSubmit={(event) => void submitFileOperation(event)}
          >
            <strong>
              {
                {
                  create: t("新建文件"),
                  mkdir: t("新建文件夹"),
                  rename: t("重命名"),
                  delete: t("删除文件"),
                }[form.action]
              }
            </strong>
            {form.action === "delete" ? (
              <p>
                {t("删除“")}
                {form.path}
                {t("”？此操作会移除磁盘上的文件或空文件夹。")}
              </p>
            ) : (
              <label>
                {t("项目相对路径")}
                <input
                  autoFocus
                  aria-label={t("文件路径")}
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
                {t("取消")}
              </button>
              <button
                className="core-primary"
                disabled={busy || (form.action !== "delete" && !form.value.trim())}
              >
                {form.action === "delete" ? t("删除") : t("确定")}
              </button>
            </div>
          </form>
        </div>
      )}
      {closePath && (
        <div className="core-dialog-backdrop">
          <div
            className="core-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={t("保存文件更改")}
          >
            <strong>{t("保存文件更改？")}</strong>
            <p>
              {closePath}
              {t("有未保存的编辑。")}
            </p>
            <div className="core-dialog-actions">
              <button onClick={() => setClosePath(undefined)}>{t("取消")}</button>
              <button onClick={() => closeDocument(closePath, true)}>{t("放弃更改")}</button>
              <button
                className="core-primary"
                disabled={busy}
                onClick={async () => {
                  if (await saveFile(closePath)) closeDocument(closePath, true);
                }}
              >
                {t("保存并关闭")}
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
            aria-label={t("比较磁盘版本")}
          >
            <strong>
              {t("比较磁盘版本 ·")}
              {diskComparison.path}
            </strong>
            <div className="core-compare-columns">
              <label>
                {t("当前编辑")}
                <textarea
                  readOnly
                  aria-label={t("比较当前编辑")}
                  value={documents.find((item) => item.path === diskComparison.path)?.content ?? ""}
                />
              </label>
              <label>
                {t("磁盘内容")}
                <textarea
                  readOnly
                  aria-label={t("比较磁盘内容")}
                  value={diskComparison.file.content ?? ""}
                />
              </label>
            </div>
            <p>
              {t(
                "可以采用磁盘内容，或保留当前编辑并更新保存基准。保留编辑后，仍需点击保存才会写入文件。",
              )}
            </p>
            <div className="core-dialog-actions">
              <button onClick={() => setDiskComparison(undefined)}>{t("关闭")}</button>
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
                {t("采用磁盘内容")}
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
                {t("保留编辑并更新保存基准")}
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
      aria-label={t("工作区文档")}
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
          className={`core-file-tab ${documentActive && !settingsActive && !diffActive && !searchEditorActive && activePath === document.path ? "active" : ""}`}
          key={document.path}
        >
          <button
            role="tab"
            tabIndex={!settingsActive && activePath === document.path ? 0 : -1}
            aria-selected={
              documentActive &&
              !settingsActive &&
              !diffActive &&
              !searchEditorActive &&
              activePath === document.path
            }
            title={document.path}
            onClick={() => {
              setSettingsActive(false);
              setSearchEditorActive(false);
              setHistory((previous) => visitDocument(previous, document.path));
              setActivePath(document.path);
              setDocumentActive(true);
              setDiffActive(false);
            }}
          >
            <FileIcon path={document.path} />
            <span>{document.path.split("/").pop()}</span>
            {document.content !== document.saved && (
              <span className="core-dirty" title={t("未保存")} />
            )}
          </button>
          <Tool
            label={t("关闭 {0}", { "0": document.path })}
            icon="close"
            onClick={() => closeDocument(document.path)}
          />
        </div>
      ))}
      {searchDocumentOpen && (
        <div
          className={`core-file-tab ${documentActive && !settingsActive && searchEditorActive ? "active" : ""}`}
        >
          <button
            role="tab"
            aria-selected={documentActive && !settingsActive && searchEditorActive}
            tabIndex={documentActive && !settingsActive && searchEditorActive ? 0 : -1}
            onClick={() => {
              setSettingsActive(false);
              setSearchEditorActive(true);
              setDocumentActive(true);
              setDiffActive(false);
            }}
          >
            <Icon name="search" size={14} />
            <span>{t("搜索结果")}</span>
          </button>
          <Tool
            label={t("关闭搜索结果")}
            icon="close"
            onClick={() => {
              setSearchDocumentOpen(false);
              setSearchEditorActive(false);
            }}
          />
        </div>
      )}
      {diffActive && (
        <div className={`core-file-tab ${settingsActive ? "" : "active"}`}>
          <button
            role="tab"
            aria-selected={!settingsActive}
            tabIndex={settingsActive ? -1 : 0}
            onClick={() => {
              setSettingsActive(false);
              setDocumentActive(true);
              setSearchEditorActive(false);
            }}
          >
            <span>
              {t("差异 ·")}
              {diff?.path.split("/").pop() ?? t("读取中")}
            </span>
          </button>
          <Tool label={t("关闭差异")} icon="close" onClick={() => setDiffActive(false)} />
        </div>
      )}
      {settingsOpen && (
        <div className={`core-file-tab ${settingsActive ? "active" : ""}`}>
          <button
            role="tab"
            aria-selected={settingsActive}
            tabIndex={settingsActive ? 0 : -1}
            onClick={() => setSettingsActive(true)}
          >
            <Icon name="settings" size={14} />
            <span>{t("设置")}</span>
          </button>
          <Tool
            label={t("关闭设置")}
            icon="close"
            onClick={(event) => {
              const tabStrip = event.currentTarget.closest(".core-document-tabs");
              setSettingsOpen(false);
              setSettingsActive(false);
              requestAnimationFrame(() => {
                const target = tabStrip?.querySelector<HTMLElement>(
                  '[role="tab"][aria-selected="true"]',
                );
                (target ?? document.querySelector<HTMLElement>(".settings-trigger"))?.focus();
              });
            }}
          />
        </div>
      )}
    </div>
  );
  const diffTitle = diff?.historical
    ? t("提交 {0} · 所选范围差异", { 0: diff.path })
    : (diff?.title ?? (diff?.staged ? t("已暂存 ↔ HEAD") : t("工作区 ↔ 暂存区")));
  const diffProjectPath =
    diff && !diff.historical ? gitPathInProject(diff.path, gitStatus?.project_prefix) : undefined;
  const editorContent = searchEditorActive ? (
    codeSearch.editor
  ) : diffActive ? (
    <section className="core-diff-document" aria-label={t("Git 差异")}>
      <div className="core-document-header">
        <span>
          {diff?.path} {diff ? `· ${diffTitle}` : ""}
        </span>
        {diffProjectPath !== undefined && (
          <button onClick={() => void openFile(diffProjectPath)}>{t("打开文件")}</button>
        )}
      </div>
      {diffLoading ? (
        <p className="core-hint">{t("正在读取差异…")}</p>
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
              : t("没有文本差异。文件可能仅有权限变更。")}
          </pre>
          {diff?.truncated && <p className="core-hint">{t("差异超过 1 MiB，显示已截断。")}</p>}
        </>
      )}
    </section>
  ) : !active ? (
    <div className="core-welcome">
      <Icon name="files" size={38} />
      <h2>{t("打开文件，开始编辑")}</h2>
      <p>{t("从文件资源管理器选择文件，或使用 ⌘ / Ctrl + P 快速打开。")}</p>
      {hasDomain && (
        <button className="core-primary" onClick={activateDomain}>
          {t("打开专业预览")}
        </button>
      )}
    </div>
  ) : active.loading ? (
    <Empty title={t("正在打开文件")}>{active.path}</Empty>
  ) : active.error || active.reason ? (
    <Empty title={active.error ? t("文件读取失败") : t("暂不支持编辑此文件")}>
      {t(active.error ?? active.reason)}
      <button
        onClick={() => {
          void rereadFile(active.path);
        }}
      >
        {t("重新读取")}
      </button>
    </Empty>
  ) : (
    <section className="core-text-document" aria-label={t("编辑 {0}", { "0": active.path })}>
      <div className="core-document-header">
        <nav className="core-breadcrumb" aria-label={t("文件路径")} title={active.path}>
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
            {active.content !== active.saved ? t("未保存") : t("已保存")}
          </span>
          <Tool
            label={t("与磁盘内容比较")}
            icon="compare"
            disabled={busy}
            onClick={() => void compareDisk(active.path)}
          />
          <Tool
            label={t("重新读取文件")}
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
            {t("保存")}
          </button>
        </div>
      </div>
      <SourceEditor
        path={active.path}
        value={active.content}
        models={sourceModels}
        visible={documentActive && !settingsActive}
        location={active.location}
        onChange={(content) => {
          setDocuments((previous) =>
            previous.map((item) =>
              item.path === active.path && item.content !== content
                ? { ...item, content, location: undefined }
                : item,
            ),
          );
        }}
        onCursorLine={(cursorLine) => {
          setDocuments((previous) =>
            previous.map((item) =>
              item.path === active.path && (item.cursorLine ?? 1) !== cursorLine
                ? { ...item, cursorLine }
                : item,
            ),
          );
        }}
      />
      <div className="core-editor-status">
        <span>UTF-8 · {active.path.split(".").pop()?.toUpperCase()}</span>
        <span>
          {t("行 {0} · 共 {1} 行", {
            0: active.cursorLine ?? 1,
            1: active.content.split("\n").length,
          })}
        </span>
      </div>
    </section>
  );
  const inspector = (
    <section className="core-file-properties">
      <div className="core-heading">
        <strong>{t("文件属性")}</strong>
      </div>
      {active && !diffActive && !searchEditorActive ? (
        <dl>
          <dt>{t("路径")}</dt>
          <dd>{active.path}</dd>
          <dt>{t("编码")}</dt>
          <dd>UTF-8</dd>
          <dt>{t("状态")}</dt>
          <dd>
            {active.loading
              ? t("读取中")
              : active.reason
                ? t("不可编辑")
                : active.content !== active.saved
                  ? t("未保存")
                  : t("已保存")}
          </dd>
          <dt>{t("行数")}</dt>
          <dd>{active.content.split("\n").length}</dd>
        </dl>
      ) : (
        <p className="core-hint">{t("选择文件后查看属性。")}</p>
      )}
    </section>
  );
  const value: Core = {
    activity: coreActivity,
    select,
    sidebar,
    editor: (
      <>
        <div className="core-editor-page" hidden={settingsActive}>
          {editorContent}
        </div>
        {settingsActive && <Settings />}
      </>
    ),
    tabs,
    inspector,
    documentActive: documentActive || settingsActive,
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
  const extensionActivity =
    activeActivity ||
    activityItems.find(({ id }) => id === layout.sidebarActivity)?.id ||
    activityItems[0]?.id ||
    "";
  const activeActivityId = core.activity ?? (extensionActivity || "explorer");
  const showCoreSidebar = isCoreActivity(activeActivityId);
  return (
    <WorkbenchLayout
      layout={layout}
      change={change}
      activity={
        <ActivityBar
          active={activeActivityId}
          items={items}
          select={(id) => {
            if (isCoreActivity(id)) core.select(id);
            else {
              change({ sidebarOpen: true, sidebarActivity: id });
              onActivity?.(id);
            }
          }}
        />
      }
      sidebar={
        <>
          <div className="core-sidebar-slot" hidden={!showCoreSidebar}>
            {core.sidebar}
          </div>
          <div className="core-sidebar-slot" hidden={showCoreSidebar}>
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
