import { t } from "./i18n";
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ActivityBar,
  activityBarPositionMenuItem,
  sidebarLayoutMenuItems,
  WorkbenchLayout,
} from "./atomstudio/Layout";
import { Empty, Icon } from "./ui";
import type { FilePreview, StudioLayout, StudioState, WorkbenchProps } from "./sdk";
import {
  gitPathInProject,
  explorerCreationParent,
  visibleFileTree,
  filePreviewKind,
  fileDocumentSnapshot,
  fileDocumentTitle,
  refreshedFileDocument,
  savedFileDocument,
  FileReadRequests,
  type FileListing,
  type GitDiff,
  type GitEntry,
  type GitStatus,
  type SearchMatch,
} from "./workspace-model";
import { visitDocument, stepDocument, type DocumentHistory } from "./navigation";
import { rememberRecentFile, type FileLocation, type EditorPickerItem } from "./search";
import { useUserPreferences, fileEditorDefault, setFileEditorDefault } from "./user-preferences";
import { useCodeSearch } from "./CodeSearch";
import { ExplorerTree, type ExplorerCreation } from "./ExplorerTree";
import {
  DesignDocument,
  DesignExplorer,
  designDocumentKey,
  resolveDesign,
  type DesignIndex,
  type DesignSelection,
  type DesignView,
  type LibrarySource,
} from "./DesignNavigation";
import {
  LibraryFolders,
  readLibraryFolders,
  requestedLibrarySource,
  saveLibraryFolders,
} from "./LibraryFolders";
import { FileIcon } from "./FileIcon";
import { EditorActionButton } from "./EditorActionButton";
import { ToolbarButton } from "./ToolbarButton";
import { NavigatorMenu, type NavigatorMenuItem } from "./NavigatorMenu";
import {
  NavigatorButton as Tool,
  NavigatorPage,
  NavigatorPanel,
  NavigatorToolbar,
} from "./NavigatorPanel";
import "./workspace.css";
import { SourceControl } from "./SourceControl";
import {
  goToSourcePosition,
  supportsSourceLineComments,
  runSourceEditCommand,
  selectedSourceText,
  SourceEditor,
  type SourceModels,
} from "./SourceEditor";
import { isSourceEditCommand, type EditorMenuCommand, type CommandItem } from "./menus";
import { Settings, type TitlebarSettings } from "./Settings";
import { Keycaps } from "./Keycaps";
import { FilePreviewContent } from "./ProjectPreview";
import { MarkdownEditor } from "./MarkdownEditor";
import { FileEditorActions, FileOpenWith } from "./FileEditorActions";
import {
  emptyEditorGroup,
  openEditorView,
  updateEditorView,
  closeEditorView,
  removeEditorDocument,
  renameEditorDocument,
  resolvedEditorPresentation,
  initializeEditorView,
  editorViewCount,
  closeEditorsInOrder,
  primaryEditorGroup,
  secondaryEditorGroup,
  type EditorGroup,
  type EditorPresentation,
  type EditorGroupTab,
  type SourceEditorStatus,
} from "./editor-groups";
export type { EditorGroupTab } from "./editor-groups";

type Activity = "explorer" | "search" | "scm" | "extensions" | "library" | "instances";
function isCoreActivity(id: string): id is Activity {
  return coreItems.some((item) => item.id === id);
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
  design?: { selection: DesignSelection; view: DesignView };
  untitled?: string;
  generated?: boolean;
  preview?: FilePreview["preview"];
};
type FileRequest = {
  path: string;
  token: number;
  location?: FileLocation;
};
type EditorGroupContent = {
  id: string;
  tabs: ReactNode;
  editorActions: ReactNode;
  editor: ReactNode;
};
type ExplorerHeading = "panel" | "workspace" | "editors";
type ExplorerHeadingMenu = {
  heading: ExplorerHeading;
  x: number;
  y: number;
  anchor: HTMLElement;
};
type Core = {
  activity: Activity;
  select: (id: Activity) => void;
  sidebar: (domainTabs: EditorGroupTab[]) => ReactNode;
  editor: ReactNode;
  editorActions: (domainTabs?: EditorGroupTab[]) => ReactNode;
  tabs: ReactNode;
  secondary?: EditorGroupContent;
  focusedGroup: string;
  focusGroup: (id: string) => void;
  inspector: ReactNode;
  documentActive: boolean;
  closeFocusRequest: number;
  activateDomain: () => void;
  activateCore: () => void;
  hasCoreTabs: boolean;
  setDomainTabsOpen: (open: boolean) => void;
  hasDomain: boolean;
  changeCount: number;
};
const CoreContext = createContext<Core | null>(null);
export const useCoreWorkspace = () => useContext(CoreContext);
const emptyListing: FileListing = { files: [], directories: [], truncated: false };
const coreItems: { id: Activity; label: string; icon: string; shortcutKey?: string }[] = [
  { id: "explorer", label: "文件资源管理器", icon: "files", shortcutKey: "E" },
  { id: "search", label: "搜索", icon: "search", shortcutKey: "F" },
  { id: "scm", label: "源代码管理", icon: "branch", shortcutKey: "G" },
  { id: "extensions", label: "扩展", icon: "extensions" },
  { id: "library", label: "部件库", icon: "library" },
  { id: "instances", label: "项目实例", icon: "system" },
];
const coreSidebarTitles: Record<Activity, string> = {
  explorer: "文件资源管理器",
  search: "搜索",
  scm: "源代码管理",
  extensions: "扩展",
  library: "部件库",
  instances: "项目实例",
};
const macKeys = /Mac|iPhone|iPad/.test(navigator.platform);
const tabCloseKeys = macKeys ? "Delete Backspace" : "Delete";

function DocumentTab({
  selected,
  tabStop = selected,
  title,
  children,
  closeLabel,
  closeDisabled,
  closeShortcut,
  onSelect,
  onClose,
}: {
  selected: boolean;
  tabStop?: boolean;
  title?: string;
  children: ReactNode;
  closeLabel: string;
  closeDisabled: boolean;
  closeShortcut?: string[];
  onSelect: () => void;
  onClose: () => void;
}) {
  return (
    <div
      className={`core-file-tab${selected ? " active" : ""}`}
      onKeyDown={(event) => {
        if (
          (event.key !== "Delete" && !(macKeys && event.key === "Backspace")) ||
          event.altKey ||
          event.ctrlKey ||
          event.metaKey ||
          event.shiftKey ||
          event.nativeEvent.isComposing ||
          event.keyCode === 229
        )
          return;
        event.preventDefault();
        if (!event.repeat && !closeDisabled) onClose();
      }}
    >
      <button
        role="tab"
        tabIndex={tabStop ? 0 : -1}
        aria-selected={selected}
        aria-keyshortcuts={closeDisabled ? undefined : tabCloseKeys}
        title={title}
        onClick={onSelect}
      >
        {children}
      </button>
      <ToolbarButton
        type="button"
        className="navigator-button"
        aria-label={closeLabel}
        aria-disabled={closeDisabled || undefined}
        aria-keyshortcuts={closeDisabled ? undefined : tabCloseKeys}
        tooltip={closeLabel}
        shortcut={!closeDisabled && selected ? closeShortcut : undefined}
        onClick={() => {
          if (!closeDisabled) onClose();
        }}
      >
        <Icon name="close" size={16} />
      </ToolbarButton>
    </div>
  );
}

function DialogLayer({ children, cancel }: { children: ReactNode; cancel: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const cancelRef = useRef(cancel);
  cancelRef.current = cancel;
  useLayoutEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input,textarea,button")?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        cancelRef.current();
      }
      if (event.key === "Tab") {
        const nodes = Array.from(
          ref.current?.querySelectorAll<HTMLElement>("input,button:not(:disabled),textarea") ?? [],
        );
        if (!nodes.length) return;
        const first = nodes[0],
          last = nodes[nodes.length - 1];
        if (!nodes.some((node) => node === document.activeElement)) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        } else if (event.shiftKey && document.activeElement === first) {
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
      if (previous?.isConnected) previous.focus();
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
  layout,
  fileRequest,
  activityRequest,
  settingsRequest,
  titlebarSettings,
  closeRequest,
  saveRequest,
  editorMenuRequest,
  shortcutCommands,
  closeShortcut,
  navigationRequest,
  onNavigationState,
  onSourceStatus,
  hasDomain,
  showEditorPicker,
  children,
}: {
  state: StudioState;
  api: WorkbenchProps["api"];
  refresh: WorkbenchProps["refresh"];
  notify: WorkbenchProps["notify"];
  changeLayout: WorkbenchProps["changeLayout"];
  layout: StudioLayout;
  fileRequest?: FileRequest;
  activityRequest?: { id: Activity; token: number; replace?: boolean };
  settingsRequest?: number;
  titlebarSettings: TitlebarSettings;
  closeRequest?: number;
  saveRequest?: number;
  editorMenuRequest?: { id: EditorMenuCommand };
  shortcutCommands: CommandItem[];
  closeShortcut?: string[];
  navigationRequest?: WorkbenchProps["navigationRequest"];
  onNavigationState?: (next: {
    active: boolean;
    canBack: boolean;
    canForward: boolean;
    canClose: boolean;
    canSave: boolean;
    canSaveAs: boolean;
    canCreateFile: boolean;
    canSaveAll: boolean;
    canRevert: boolean;
    canEditSource: boolean;
    canReadSource: boolean;
    canCommentSource: boolean;
    canUndo: boolean;
    canRedo: boolean;
  }) => void;
  onSourceStatus?: (status?: SourceEditorStatus) => void;
  hasDomain: boolean;
  showEditorPicker: (items: EditorPickerItem[]) => void;
  children: ReactNode;
}) {
  const { autoSave, confirmBeforeUnload, fileAssociations } = useUserPreferences();
  const sidebarActivity = layout.sidebarActivity ?? "explorer";
  const activity = isCoreActivity(sidebarActivity) ? sidebarActivity : "explorer";
  const [listing, setListing] = useState<FileListing>(emptyListing),
    [listingError, setListingError] = useState("");
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [designIndex, setDesignIndex] = useState<DesignIndex>();
  const [designError, setDesignError] = useState("");
  const [libraryFolders, setLibraryFolders] = useState(readLibraryFolders);
  const [libraryDialog, setLibraryDialog] = useState(false);
  const [libraryRevision, setLibraryRevision] = useState(0);
  function changeLibraryFolders(paths: string[]) {
    saveLibraryFolders(paths);
    setLibraryFolders(paths);
  }
  async function addLibraryFolder(path: string) {
    const index = await api<DesignIndex>("/api/library", { action: "index", folders: [path] });
    const source = requestedLibrarySource(index, path);
    if (!source) throw new Error(index.source_errors[0]?.message ?? t("无法读取库目录"));
    if (libraryFolders.includes(source.path)) throw new Error(t("此库目录已经添加。"));
    if (libraryFolders.length >= 16) throw new Error(t("最多可添加 16 个库目录。"));
    changeLibraryFolders([...libraryFolders, source.path]);
  }
  async function referenceLibrary(source: LibrarySource, entry: string, previousDigest?: string) {
    if (documents.some((item) => item.path === "texsys.toml" && item.saved !== item.content)) {
      throw new Error(t("请先保存项目清单的修改。"));
    }
    await api("/api/library", {
      action: "reference",
      folder: source.path,
      digest: source.digest,
      entry,
      previous_digest: previousDigest,
    });
    setLibraryRevision((previous) => previous + 1);
    await refresh();
  }
  useEffect(() => {
    let current = true;
    const request = libraryFolders.length
      ? api<DesignIndex>("/api/library", { action: "index", folders: libraryFolders })
      : api<DesignIndex>("/api/design-index");
    void request.then(
      (index) => {
        if (current) {
          setDesignIndex({
            ...index,
            sources: index.sources ?? [],
            source_errors: index.source_errors ?? [],
            references: index.references ?? [],
          });
          setDesignError("");
        }
      },
      (error: unknown) => {
        if (current) {
          setDesignIndex(undefined);
          setDesignError(String(error));
        }
      },
    );
    return () => {
      current = false;
    };
  }, [api, libraryFolders, libraryRevision, state.project.revision, state.project.path]);
  const [expanded, setExpanded] = useState(new Set<string>(["src", "assets"]));
  const [explorerMenu, setExplorerMenu] = useState<{ x: number; y: number; path: string }>();
  const [headingMenu, setHeadingMenu] = useState<ExplorerHeadingMenu>();
  const [workspaceOpen, setWorkspaceOpen] = useState(true);
  const [openEditorsExpanded, setOpenEditorsExpanded] = useState(true);
  const workspaceTreeId = React.useId();
  const openEditorsId = React.useId();
  const workspaceToggleRef = useRef<HTMLButtonElement>(null);
  const [rootOpen, setRootOpen] = useState(true),
    [selected, setSelected] = useState("");
  const projectRootRef = useRef<HTMLButtonElement>(null);
  const explorerRef = useRef<HTMLDivElement>(null);
  const [creation, setCreation] = useState<ExplorerCreation>();
  const [creating, setCreating] = useState(false);
  const [treeFocusRequest, setTreeFocusRequest] = useState<{ path: string; token: number }>();
  const explorerFocusSequence = useRef(0);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [editorGroups, setEditorGroups] = useState<EditorGroup[]>([emptyEditorGroup()]);
  const [focusedGroup, setFocusedGroup] = useState(primaryEditorGroup);
  const groupsRef = useRef(editorGroups);
  groupsRef.current = editorGroups;
  const sourceStatuses = useRef(new Map<string, { path: string; status: SourceEditorStatus }>());
  const focusedGroupRef = useRef(focusedGroup);
  focusedGroupRef.current = focusedGroup;
  const focusGroup = useCallback((id: string) => {
    focusedGroupRef.current = id;
    setFocusedGroup(id);
  }, []);
  const activeGroup = editorGroups.find((group) => group.id === focusedGroup) ?? editorGroups[0];
  const activePath = activeGroup.activePath;
  const activeView = activeGroup.views.find((view) => view.path === activePath);
  const setActivePath = (path: string | null, groupId = focusedGroupRef.current) => {
    const existing = documentsRef.current.find((item) => item.path === path);
    const kind = path
      ? filePreviewKind(
          existing ?? { path, preview: listing.files.find((item) => item.path === path)?.preview },
        )
      : undefined;
    const known = path && ((!existing?.loading && existing) || kind || path.startsWith("\0"));
    setEditorGroups((groups) => {
      const opened = openEditorView(groups, groupId, path);
      return known && path
        ? initializeEditorView(
            opened,
            groupId,
            path,
            fileEditorDefault(path, kind, fileAssociations),
          )
        : opened;
    });
  };
  const updateView = (
    groupId: string,
    path: string,
    patch: Parameters<typeof updateEditorView>[3],
  ) => {
    setEditorGroups((groups) => updateEditorView(groups, groupId, path, patch));
  };
  useEffect(() => {
    if (!editorGroups.some((group) => group.id === focusedGroup)) focusGroup(primaryEditorGroup);
  }, [editorGroups, focusedGroup, focusGroup]);
  const [documentActive, setDocumentActive] = useState(true);
  const [domainTabsOpen, setDomainTabsOpen] = useState(false);
  const domainTabsOpenRef = useRef(domainTabsOpen);
  domainTabsOpenRef.current = domainTabsOpen;
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsActive, setSettingsActive] = useState(false);
  const [settingsCategoryRequest, setSettingsCategoryRequest] = useState<{
    category: "editor";
    token: number;
  }>();
  const [searchEditorActive, setSearchEditorActive] = useState(false);
  const [searchDocumentOpen, setSearchDocumentOpen] = useState(false);
  const [history, setHistory] = useState<DocumentHistory>({ entries: [], index: -1 });
  const [busy, setBusy] = useState(false);
  const [searchGeneration, setSearchGeneration] = useState(0);
  const [gitScope, setGitScope] = useState<"project" | "repository">("project");
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
    action: "rename" | "delete" | "saveAs";
    path: string;
    value: string;
    closeAfter?: boolean;
    remainingDrafts?: string[];
  }>();
  const [formError, setFormError] = useState("");
  useEffect(() => {
    if (form && !["rename", "delete", "saveAs"].includes(form.action)) setForm(undefined);
  }, [form]);
  const [closePath, setClosePath] = useState<string>();
  const pendingClose = useRef<
    { path: string; groupId: string; resolve: (closed: boolean) => void } | undefined
  >(undefined);
  const [closingGroup, setClosingGroup] = useState(false);
  const [revertPath, setRevertPath] = useState<string>();
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [linePrompt, setLinePrompt] = useState<{
    path: string;
    input: string;
    invalid?: boolean;
  }>();
  const [closeFocusRequest, setCloseFocusRequest] = useState(0);
  const searchInput = useRef<HTMLInputElement>(null);
  const [sourceFocus, setSourceFocus] = useState<{
    path: string;
    groupId: string;
    origin: Element | null;
  }>();
  const fileReads = useRef(new FileReadRequests());
  const untitledSequence = useRef(0);
  const fileWriteInFlight = useRef(false);
  const failedAutoSaves = useRef(new Map<string, { content: string; version: string }>());
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
  const previewKind = useMemo(
    () => filePreviewKind(active),
    [active?.path, active?.preview?.kind, active?.content],
  );
  const activePresentation = resolvedEditorPresentation(previewKind, activeView?.presentation);
  const activeTab =
    focusedGroup === primaryEditorGroup && settingsActive
      ? "settings"
      : focusedGroup === primaryEditorGroup && !documentActive
        ? null
        : focusedGroup === primaryEditorGroup && searchEditorActive
          ? "search"
          : focusedGroup === primaryEditorGroup && diffActive
            ? "diff"
            : active
              ? "source"
              : null;
  const terminalFile =
    activeTab === "source" &&
    !active?.design &&
    !active?.untitled &&
    !active?.loading &&
    !active?.error
      ? active?.path
      : undefined;
  useEffect(() => {
    const execute = (command: string) => {
      window.dispatchEvent(new CustomEvent("texsys:terminal:runCommand", { detail: { command } }));
    };
    const runFile = () => {
      const extension = terminalFile?.split(".").at(-1)?.toLowerCase();
      const runner =
        extension === "sh" || extension === "bash"
          ? "bash"
          : extension === "zsh"
            ? "zsh"
            : extension === "py"
              ? "python3"
              : null;
      if (!terminalFile || !runner) {
        notify(t("请打开已保存的 Shell 或 Python 脚本。"), true);
        return;
      }
      const path = "'" + `./${terminalFile}`.replaceAll("'", "'\"'\"'") + "'";
      execute(`${runner} ${path}`);
    };
    const runSelection = () => {
      const selection =
        window.getSelection()?.toString() ||
        (terminalFile ? selectedSourceText(terminalFile, sourceModels, focusedGroup) : "");
      if (selection.trim()) execute(selection);
      else notify(t("请先选择要在终端运行的文本。"), true);
    };
    window.addEventListener("texsys:terminal:runActiveFile", runFile);
    window.addEventListener("texsys:terminal:runSelectedText", runSelection);
    return () => {
      window.removeEventListener("texsys:terminal:runActiveFile", runFile);
      window.removeEventListener("texsys:terminal:runSelectedText", runSelection);
    };
  }, [notify, sourceModels, terminalFile, focusedGroup]);
  const closeDisabled = !!(
    busy ||
    closingGroup ||
    form ||
    closePath ||
    revertPath ||
    linePrompt ||
    showShortcuts
  );
  const canClose = !!activeTab && !closeDisabled;
  const canReadSource =
    activeTab === "source" &&
    !!active &&
    !active.design &&
    !active.loading &&
    !active.error &&
    !active.reason &&
    !closeDisabled &&
    activePresentation === "source";
  const sourceStatusPath =
    activeTab === "source" &&
    !active?.design &&
    !active?.loading &&
    !active?.error &&
    !active?.reason &&
    activePresentation === "source"
      ? active?.path
      : undefined;
  useLayoutEffect(() => {
    const snapshot = sourceStatuses.current.get(activeGroup.id);
    onSourceStatus?.(snapshot?.path === sourceStatusPath ? snapshot?.status : undefined);
  }, [activeGroup.id, sourceStatusPath, onSourceStatus]);
  const canEditSource = canReadSource;
  const canSave =
    activeTab === "source" &&
    !!active &&
    !active.design &&
    !active.loading &&
    !active.error &&
    !active.reason &&
    !closeDisabled &&
    (!!active.untitled || active.content !== active.saved);
  const canSaveAs =
    activeTab === "source" &&
    !!active &&
    !active.design &&
    !active.loading &&
    !active.error &&
    !active.reason &&
    !closeDisabled;
  const canCommentSource = canEditSource && supportsSourceLineComments(active.path);
  const canRevert = canSaveAs && !active.untitled;
  const dirty = documents.some((item) => item.content !== item.saved);
  const canSaveAll = dirty && !closeDisabled;
  const select = useCallback(
    (id: Activity) => changeLayout({ sidebarOpen: true, sidebarActivity: id }),
    [changeLayout],
  );
  const hasCoreTabs =
    editorGroups[0].views.length > 0 || searchDocumentOpen || diffActive || settingsOpen;
  useEffect(() => {
    if (
      !closingGroup &&
      !hasCoreTabs &&
      !domainTabsOpen &&
      focusedGroup === primaryEditorGroup &&
      editorGroups.some((group) => group.id === secondaryEditorGroup)
    ) {
      focusGroup(secondaryEditorGroup);
      setCloseFocusRequest((request) => request + 1);
    }
  }, [closingGroup, hasCoreTabs, domainTabsOpen, focusedGroup, editorGroups, focusGroup]);
  const activateCore = useCallback(() => {
    setDocumentActive(true);
  }, []);
  const activateDomain = useCallback(() => {
    focusGroup(primaryEditorGroup);
    setSettingsActive(false);
    setDocumentActive(false);
    setSearchEditorActive(false);
    setDiffActive(false);
  }, [focusGroup]);
  const readFiles = useCallback(async () => {
    setLoadingFiles(true);
    try {
      const next = await api<FileListing>("/api/files");
      setListing(next);
      setDocuments((previous) =>
        previous.map((item) => {
          const file = next.files.find((entry) => entry.path === item.path);
          return file ? { ...item, generated: !!file.generated, preview: file.preview } : item;
        }),
      );
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
    const terminalCompleted = () => {
      void readGit();
      void readFiles();
    };
    window.addEventListener("texsys:terminal:completed", terminalCompleted);
    const timer = setInterval(() => {
      if (activity === "scm" && !busy) void readGit();
    }, 5000);
    return () => {
      window.removeEventListener("focus", focus);
      window.removeEventListener("texsys:terminal:completed", terminalCompleted);
      clearInterval(timer);
    };
  }, [readFiles, readGit, activity, busy]);
  useEffect(() => {
    void readFiles();
  }, [state.project.revision, readFiles]);
  useEffect(() => {
    if (!dirty || !confirmBeforeUnload) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty, confirmBeforeUnload]);
  const openDesign = (selection: DesignSelection, view?: DesignView, recordHistory = true) => {
    focusGroup(primaryEditorGroup);
    const path = designDocumentKey(selection);
    setSettingsActive(false);
    setSearchEditorActive(false);
    setDiffActive(false);
    setActivePath(path);
    setDocumentActive(true);
    if (recordHistory) setHistory((previous) => visitDocument(previous, path));
    setDocuments((previous) =>
      previous.some((item) => item.path === path)
        ? previous.map((item) =>
            item.path === path && view ? { ...item, design: { selection, view } } : item,
          )
        : [
            ...previous,
            {
              path,
              saved: "",
              content: "",
              version: "",
              design: { selection, view: view ?? "model" },
            },
          ],
    );
  };
  const openFile = async (
    path: string,
    {
      location,
      recordHistory = true,
      focus = false,
      fromDiskSearch = false,
      groupId = focusedGroupRef.current,
      presentation,
    }: {
      location?: FileLocation & { end_column?: number };
      recordHistory?: boolean;
      focus?: boolean;
      fromDiskSearch?: boolean;
      groupId?: string;
      presentation?: EditorPresentation;
    } = {},
  ) => {
    focusGroup(groupId);
    if (path.startsWith("\0untitled:")) {
      if (!documentsRef.current.some((item) => item.path === path)) return;
      if (groupId === primaryEditorGroup) {
        setSettingsActive(false);
        setSearchEditorActive(false);
        setDiffActive(false);
      }
      setActivePath(path, groupId);
      if (groupId === primaryEditorGroup) setDocumentActive(true);
      if (recordHistory) setHistory((previous) => visitDocument(previous, path));
      return;
    }
    if (path.startsWith("\0definition:")) {
      openDesign({ definition: path.slice("\0definition:".length) }, undefined, recordHistory);
      return;
    }
    if (path.startsWith("\0instance:")) {
      const occurrence = path.slice("\0instance:".length);
      openDesign({ occurrence: occurrence ? occurrence.split(".") : [] }, undefined, recordHistory);
      return;
    }
    setSourceFocus(focus ? { path, groupId, origin: document.activeElement } : undefined);
    if (groupId === primaryEditorGroup) setSettingsActive(false);
    if (recordHistory) setHistory((previous) => visitDocument(previous, path));
    if (groupId === primaryEditorGroup) setSearchEditorActive(false);
    setWorkspaceOpen(true);
    setRootOpen(true);
    setSelected(path);
    setActivePath(path, groupId);
    if (groupId === primaryEditorGroup) setDocumentActive(true);
    if (groupId === primaryEditorGroup) setDiffActive(false);
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
    updateView(groupId, path, {
      location: target,
      ...(presentation || target ? { presentation: presentation ?? "source" } : {}),
    });
    const existing = documentsRef.current.find((item) => item.path === path);
    const hasEdits = existing && existing.content !== existing.saved;
    if (hasEdits || (focus && existing && !existing.loading && !existing.error)) {
      rememberRecentFile(state.project.path, path);
      fileReads.current.invalidate(path);
      if (target && hasEdits && fromDiskSearch) {
        updateView(groupId, path, { location: undefined });
        notify(t("文件有未保存的编辑，已打开文件；请保存后再按磁盘搜索结果定位。"));
      } else if (focus) {
        setDocuments((previous) =>
          previous.map((item) => (item.path === path ? { ...item, location: target } : item)),
        );
      }
      return;
    }
    const read = fileReads.current.begin(path);
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
      if (!read.isCurrent()) return;
      const snapshot = fileDocumentSnapshot(path, file);
      setEditorGroups((groups) =>
        initializeEditorView(
          groups,
          groupId,
          path,
          fileEditorDefault(path, filePreviewKind({ path, ...snapshot }), fileAssociations),
        ),
      );
      rememberRecentFile(state.project.path, path);
      setDocuments((previous) =>
        read.update(previous, (items) =>
          items.map((item) =>
            item.path === path && item.content === item.saved
              ? { ...item, location: target, ...snapshot }
              : item,
          ),
        ),
      );
    } catch (error) {
      if (!read.isCurrent()) return;
      setDocuments((previous) =>
        read.update(previous, (items) =>
          items.map((item) =>
            item.path === path ? { ...item, loading: false, error: String(error) } : item,
          ),
        ),
      );
    }
  };
  useEffect(() => {
    const model = canEditSource ? sourceModels.get(active.path)?.model : undefined;
    onNavigationState?.({
      active: focusedGroup !== primaryEditorGroup || documentActive || settingsActive,
      canBack: (focusedGroup !== primaryEditorGroup || !settingsActive) && history.index > 0,
      canForward:
        (focusedGroup !== primaryEditorGroup || !settingsActive) &&
        history.index < history.entries.length - 1,
      canClose,
      canSave,
      canSaveAs,
      canCreateFile: !closeDisabled,
      canSaveAll,
      canRevert,
      canEditSource,
      canReadSource,
      canCommentSource,
      canUndo: model?.canUndo() ?? false,
      canRedo: model?.canRedo() ?? false,
    });
  }, [
    documentActive,
    settingsActive,
    history.index,
    history.entries.length,
    canClose,
    canSave,
    canSaveAs,
    canSaveAll,
    canRevert,
    closeDisabled,
    canEditSource,
    canReadSource,
    canCommentSource,
    active,
    sourceModels,
    onNavigationState,
    focusedGroup,
  ]);
  useEffect(() => {
    if (
      (focusedGroup === primaryEditorGroup && (!documentActive || settingsActive)) ||
      !navigationRequest ||
      !["navigation.back", "navigation.forward"].includes(navigationRequest.command)
    )
      return;
    const next = stepDocument(history, navigationRequest.command === "navigation.back" ? -1 : 1);
    if (next === history) return;
    setHistory(next);
    void openFile(next.entries[next.index], { recordHistory: false });
  }, [navigationRequest?.token]);
  useEffect(() => {
    if (fileRequest)
      void openFile(fileRequest.path, { location: fileRequest.location, focus: true });
  }, [fileRequest?.token]);
  useLayoutEffect(() => {
    if (
      sourceFocus &&
      (sourceFocus.path !== activePath ||
        sourceFocus.groupId !== focusedGroup ||
        activeTab !== "source")
    )
      setSourceFocus(undefined);
  }, [sourceFocus, activePath, activeTab, focusedGroup]);
  useEffect(() => {
    if (!settingsRequest) return;
    focusGroup(primaryEditorGroup);
    setSettingsOpen(true);
    setSettingsActive(true);
  }, [settingsRequest]);
  const saveFiles = async (paths: string[], automatic = false): Promise<boolean> => {
    if (fileWriteInFlight.current) return false;
    fileWriteInFlight.current = true;
    setBusy(true);
    const errors: string[] = [];
    let count = 0;
    try {
      for (const path of paths) {
        const document = documentsRef.current.find((item) => item.path === path);
        if (!document?.version || document.content === document.saved) continue;
        fileReads.current.invalidate(path);
        try {
          const saved = await api<FilePreview>("/api/file", {
            action: "save",
            path,
            content: document.content,
            version: document.version,
          });
          const nextDocuments = documentsRef.current.map((item) =>
            item.path === path ? savedFileDocument(item, saved) : item,
          );
          documentsRef.current = nextDocuments;
          setDocuments(nextDocuments);
          failedAutoSaves.current.delete(path);
          count += 1;
        } catch (error) {
          failedAutoSaves.current.set(path, {
            content: document.content,
            version: document.version,
          });
          errors.push(`${path}: ${error instanceof Error ? error.message : String(error)}`);
        }
      }
      if (count) await reload();
      if (errors.length) notify(errors.join("\n"), true);
      else if (count && !automatic) notify(t(count === 1 ? "文件已保存" : "全部文件已保存"));
      return errors.length === 0;
    } finally {
      fileWriteInFlight.current = false;
      setBusy(false);
    }
  };
  const startSaveAs = (path: string, closeAfter = false, remainingDrafts: string[] = []) => {
    setFormError("");
    setForm({
      action: "saveAs",
      path,
      value: path.startsWith("\0untitled:") ? "" : path,
      closeAfter,
      remainingDrafts,
    });
  };
  const saveFile = (path = activePath) => {
    const document = documentsRef.current.find((item) => item.path === path);
    if (!document) return Promise.resolve(false);
    if (document.untitled) {
      startSaveAs(document.path);
      return Promise.resolve(false);
    }
    return saveFiles([document.path]);
  };
  const saveAll = async () => {
    const pending = documentsRef.current.filter((item) => item.content !== item.saved);
    const named = pending.filter((item) => !item.untitled).map((item) => item.path);
    if (named.length) await saveFiles(named);
    const [first, ...remaining] = pending.filter((item) => item.untitled);
    if (first)
      startSaveAs(
        first.path,
        false,
        remaining.map((item) => item.path),
      );
  };
  useEffect(() => {
    if (!autoSave || !canSaveAll) return;
    const pending = documents.filter((item) => {
      const failed = failedAutoSaves.current.get(item.path);
      return (
        item.version &&
        item.content !== item.saved &&
        (failed?.version !== item.version || failed.content !== item.content)
      );
    });
    if (!pending.length) return;
    const timer = setTimeout(
      () =>
        void saveFiles(
          pending.map((item) => item.path),
          true,
        ),
      1000,
    );
    return () => clearTimeout(timer);
  }, [autoSave, canSaveAll, documents]);
  const rereadFile = async (path: string) => {
    const original = documentsRef.current.find((item) => item.path === path);
    if (!original || original.untitled) return;
    const read = fileReads.current.begin(path);
    try {
      const file = await api<FilePreview>(`/api/file?path=${encodeURIComponent(path)}`);
      if (!read.isCurrent()) return;
      if (!file.previewable && original.content !== original.saved)
        throw new Error(file.reason ?? t("磁盘版本无法作为文本读取。"));
      const snapshot = fileDocumentSnapshot(path, file);
      setDocuments((previous) =>
        read.update(previous, (items) =>
          items.map((item) =>
            item.path === path &&
            item.content === original.content &&
            item.version === original.version
              ? { ...item, ...snapshot, location: undefined }
              : item,
          ),
        ),
      );
    } catch (error) {
      if (!read.isCurrent()) return;
      if (original.content === original.saved)
        setDocuments((previous) =>
          read.update(previous, (items) =>
            items.map((item) =>
              item.path === path && item.content === item.saved
                ? { ...item, error: String(error) }
                : item,
            ),
          ),
        );
      notify(String(error), true);
    }
  };
  const visibleFilePaths = JSON.stringify([
    ...new Set(
      editorGroups
        .filter(
          (group) =>
            group.id !== primaryEditorGroup ||
            (documentActive && !settingsActive && !searchEditorActive && !diffActive),
        )
        .map((group) => group.activePath)
        .filter((path): path is string => !!path && !path.startsWith("\0")),
    ),
  ]);
  useEffect(() => {
    const paths: string[] = JSON.parse(visibleFilePaths);
    let closed = false;
    const cancelReads = new Map<string, () => void>();
    let timer: ReturnType<typeof setTimeout>;
    const pollPath = async (path: string) => {
      const original = documentsRef.current.find((item) => item.path === path);
      if (fileWriteInFlight.current || !original || original.content !== original.saved) return;
      const read = fileReads.current.begin(path);
      cancelReads.set(path, read.cancel);
      try {
        const file = await api<FilePreview>(`/api/file?path=${encodeURIComponent(path)}`);
        if (closed || !read.isCurrent()) return;
        fileDocumentSnapshot(path, file);
        setDocuments((previous) =>
          read.update(previous, (items) =>
            items.map((item) =>
              item.path === path ? refreshedFileDocument(item, file, original.version) : item,
            ),
          ),
        );
      } catch (error) {
        if (!closed && read.isCurrent())
          setDocuments((previous) =>
            read.update(previous, (items) =>
              items.map((item) =>
                item.path === path &&
                item.content === item.saved &&
                item.version === original.version
                  ? { ...item, error: String(error), loading: false }
                  : item,
              ),
            ),
          );
      }
    };
    const poll = async () => {
      await Promise.all(paths.map(pollPath));
      if (!closed) timer = setTimeout(() => void poll(), 2000);
    };
    void poll();
    return () => {
      closed = true;
      for (const cancel of cancelReads.values()) cancel();
      clearTimeout(timer);
    };
  }, [api, visibleFilePaths]);
  const focusSurvivingGroup = (groups: EditorGroup[]) => {
    if (!groups[0].views.length && focusedGroupRef.current === primaryEditorGroup) {
      if (settingsOpen) setSettingsActive(true);
      else if (searchDocumentOpen) {
        setSearchEditorActive(true);
        setDocumentActive(true);
      } else if (!diffActive && domainTabsOpenRef.current) activateDomain();
    }
    const primaryEmpty =
      !groups[0].views.length &&
      !settingsOpen &&
      !searchDocumentOpen &&
      !diffActive &&
      !domainTabsOpenRef.current;
    if (primaryEmpty && groups.some((group) => group.id === secondaryEditorGroup))
      focusGroup(secondaryEditorGroup);
    else if (!groups.some((group) => group.id === focusedGroupRef.current))
      focusGroup(primaryEditorGroup);
  };
  const removeDocument = (path: string) => {
    const nextGroups = removeEditorDocument(groupsRef.current, path);
    focusSurvivingGroup(nextGroups);
    groupsRef.current = nextGroups;
    setEditorGroups(nextGroups);
    fileReads.current.invalidate(path);
    failedAutoSaves.current.delete(path);
    if (path.startsWith("\0untitled:")) {
      setHistory(({ entries, index }) => ({
        entries: entries.filter((entry) => entry !== path),
        index: entries.slice(0, index + 1).filter((entry) => entry !== path).length - 1,
      }));
    }
    const nextDocuments = documentsRef.current.filter((item) => item.path !== path);
    documentsRef.current = nextDocuments;
    setDocuments(nextDocuments);
    if (activePath === path && nextDocuments.length === 0) {
      if (settingsOpen) setSettingsActive(true);
      else if (searchDocumentOpen) setSearchEditorActive(true);
      else if (domainTabsOpenRef.current) activateDomain();
    }
    setClosePath(undefined);
  };
  const finishClosingTab = (close: () => void) => {
    close();
    setCloseFocusRequest((request) => request + 1);
  };
  const closeFileView = (path: string, groupId: string) => {
    const nextGroups = closeEditorView(groupsRef.current, groupId, path);
    finishClosingTab(() => {
      if (editorViewCount(nextGroups, path) === 0) removeDocument(path);
      else {
        focusSurvivingGroup(nextGroups);
        groupsRef.current = nextGroups;
        setEditorGroups(nextGroups);
      }
    });
  };
  const finishPendingClose = (accepted: boolean) => {
    const pending = pendingClose.current;
    pendingClose.current = undefined;
    setClosePath(undefined);
    if (!pending) return;
    if (accepted) closeFileView(pending.path, pending.groupId);
    pending.resolve(accepted);
  };
  const closeFileTab = (path: string, groupId = focusedGroupRef.current): Promise<boolean> => {
    if (
      !groupsRef.current
        .find((group) => group.id === groupId)
        ?.views.some((view) => view.path === path)
    )
      return Promise.resolve(true);
    if (pendingClose.current) return Promise.resolve(false);
    focusGroup(groupId);
    const document = documentsRef.current.find((item) => item.path === path);
    if (
      editorViewCount(groupsRef.current, path) === 1 &&
      document &&
      document.saved !== document.content
    ) {
      setClosePath(path);
      return new Promise((resolve) => {
        pendingClose.current = { path, groupId, resolve };
      });
    }
    closeFileView(path, groupId);
    return Promise.resolve(true);
  };
  const closeSearchTab = (restore = true) =>
    finishClosingTab(() => {
      setSearchDocumentOpen(false);
      setSearchEditorActive(false);
      if (!restore) return;
      if (groupsRef.current[0].activePath) {
        focusGroup(primaryEditorGroup);
        setDocumentActive(true);
      } else if (settingsOpen) {
        setSettingsActive(true);
      } else if (diffActive) {
        setDocumentActive(true);
      } else if (domainTabsOpenRef.current) {
        activateDomain();
      } else if (groupsRef.current.some((group) => group.id === secondaryEditorGroup)) {
        focusGroup(secondaryEditorGroup);
      }
    });
  const closeDiffTab = (restore = true) =>
    finishClosingTab(() => {
      setDiffActive(false);
      if (!restore) return;
      if (groupsRef.current[0].activePath) {
        focusGroup(primaryEditorGroup);
        setDocumentActive(true);
      } else if (settingsOpen) {
        setSettingsActive(true);
      } else if (searchDocumentOpen) {
        setSearchEditorActive(true);
        setDocumentActive(true);
      } else if (domainTabsOpenRef.current) {
        activateDomain();
      } else if (groupsRef.current.some((group) => group.id === secondaryEditorGroup)) {
        focusGroup(secondaryEditorGroup);
      }
    });
  const closeSettingsTab = (restore = true) =>
    finishClosingTab(() => {
      setSettingsOpen(false);
      setSettingsActive(false);
      if (!restore) return;
      if (groupsRef.current[0].activePath) {
        focusGroup(primaryEditorGroup);
        setDocumentActive(true);
      } else if (searchDocumentOpen) {
        setSearchEditorActive(true);
        setDocumentActive(true);
      } else if (diffActive) {
        setDocumentActive(true);
      } else if (domainTabsOpenRef.current) {
        activateDomain();
      } else if (groupsRef.current.some((group) => group.id === secondaryEditorGroup)) {
        focusGroup(secondaryEditorGroup);
      }
    });
  useEffect(() => {
    if (!closeRequest || !canClose) return;
    if (activeTab === "settings") closeSettingsTab();
    if (activeTab === "search") closeSearchTab();
    if (activeTab === "diff") closeDiffTab();
    if (activeTab === "source" && activePath) closeFileTab(activePath);
  }, [closeRequest]);
  useEffect(() => {
    if (saveRequest && canSave) void saveFile();
  }, [saveRequest]);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
      if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
      if (event.target instanceof Element && event.target.closest('[aria-modal="true"]')) return;
      if (!event.shiftKey && event.key.toLowerCase() === "s" && activeTab === "source") {
        event.preventDefault();
        if (canSave) void saveFile();
      }
      const item = event.shiftKey
        ? coreItems.find(({ shortcutKey }) => shortcutKey === event.key.toUpperCase())
        : undefined;
      if (item) {
        event.preventDefault();
        select(item.id);
        if (item.id === "search") requestAnimationFrame(() => searchInput.current?.focus());
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [activeTab, canSave, activePath, documents, select]);
  const moveDocument = (previousPath: string, nextPath: string, file?: FilePreview) => {
    fileReads.current.invalidate(previousPath);
    failedAutoSaves.current.delete(previousPath);
    const sourceModel = sourceModels.get(previousPath);
    if (sourceModel) {
      sourceModels.delete(previousPath);
      sourceModels.set(nextPath, sourceModel);
    }
    setEditorGroups((groups) => renameEditorDocument(groups, previousPath, nextPath));
    setDocuments((previous) =>
      previous.map((item) =>
        item.path === previousPath
          ? {
              ...item,
              path: nextPath,
              untitled: undefined,
              ...(file ? fileDocumentSnapshot(nextPath, file) : {}),
            }
          : item,
      ),
    );
    setHistory((previous) => ({
      ...previous,
      entries: previous.entries.map((path) => (path === previousPath ? nextPath : path)),
    }));
  };
  const submitFileOperation = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form || fileWriteInFlight.current) return;
    if (
      form.action !== "saveAs" &&
      documentsRef.current.some((item) => item.path === form.path && item.content !== item.saved)
    ) {
      setFormError(t("请先保存或关闭该文件的未保存编辑。"));
      return;
    }
    fileWriteInFlight.current = true;
    setBusy(true);
    setFormError("");
    try {
      if (form.action === "saveAs") {
        const source = documentsRef.current.find((item) => item.path === form.path);
        if (!source) return;
        const saved = await api<FilePreview>("/api/file", {
          action: "create_text",
          path: form.value,
          content: source.content,
        });
        const current = documentsRef.current.find((item) => item.path === source.path);
        const canReplaceTab = current?.content === source.content;
        if (canReplaceTab) {
          if (form.closeAfter) finishPendingClose(true);
          else moveDocument(source.path, saved.path, saved);
        } else if (form.closeAfter) finishPendingClose(false);
        setForm(undefined);
        await reload();
        if (!form.closeAfter || !canReplaceTab) await openFile(saved.path, { focus: true });
        const [next, ...remaining] = form.remainingDrafts ?? [];
        if (next) startSaveAs(next, false, remaining);
        notify(t("文件已保存"));
        return;
      }
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
        path: form.path,
        target: form.value,
        version,
      });
      if (form.action === "delete") removeDocument(form.path);
      else if (form.action === "rename") {
        const originals = documentsRef.current.filter(
          (item) => item.path === form.path || item.path.startsWith(`${form.path}/`),
        );
        for (const item of originals)
          moveDocument(item.path, form.value + item.path.slice(form.path.length));
      }
      setForm(undefined);
      setSelected("");
      await reload();
      if (form.action === "rename" && !listing.directories.includes(form.path))
        await openFile(form.value);
    } catch (error) {
      setFormError(String(error));
    } finally {
      fileWriteInFlight.current = false;
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
          removeDocument(path);
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
    focusGroup(primaryEditorGroup);
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
    onOpenFile: (path, match) => void openFile(path, { location: match, fromDiskSearch: true }),
    onOpenEditor: () => {
      setSettingsActive(false);
      setSearchDocumentOpen(true);
      focusGroup(primaryEditorGroup);
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
  const moduleDirectories = new Map<string, string>();
  for (const entry of designIndex?.library ?? []) {
    if (entry.folder) moduleDirectories.set(entry.folder, entry.definition);
  }
  const tree = visibleFileTree(listing, expanded).map((entry) =>
    entry.directory && moduleDirectories.has(entry.path)
      ? { ...entry, icon: "module", opensDocument: true }
      : entry,
  );
  useEffect(() => {
    if (!activityRequest) return;
    select(activityRequest.id);
    if (activityRequest.id === "search") codeSearch.focus(activityRequest.replace);
  }, [activityRequest?.token]);
  const focusTreePath = (path: string) => {
    setSelected(path);
    if (!path) (workspaceOpen ? projectRootRef : workspaceToggleRef).current?.focus();
    else setTreeFocusRequest({ path, token: ++explorerFocusSequence.current });
  };
  const revealDirectory = (path: string) => {
    setWorkspaceOpen(true);
    setRootOpen(true);
    setExpanded((previous) => {
      const next = new Set(previous);
      const parts = path.split("/").filter(Boolean);
      while (parts.length) {
        next.add(parts.join("/"));
        parts.pop();
      }
      return next;
    });
  };
  const startCreation = (action: "create" | "mkdir", path = selected) => {
    if (busy) return;
    const parent = explorerCreationParent(listing, path);
    select("explorer");
    revealDirectory(parent);
    setTreeFocusRequest(undefined);
    setCreation((previous) => ({
      action,
      parent,
      value: previous?.value ?? "",
      focus: ++explorerFocusSequence.current,
    }));
  };
  const submitCreation = async () => {
    if (!creation || fileWriteInFlight.current) return;
    const name = creation.value;
    let error = "";
    if (!name.trim()) error = t("请输入名称。");
    else if (
      name !== name.trim() ||
      name === "." ||
      name === ".." ||
      /[/\\]/.test(name) ||
      Array.from(name).some(
        (character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
      )
    )
      error = t("请输入有效名称，不能包含路径分隔符、控制字符或首尾空格。");
    const path = creation.parent ? `${creation.parent}/${name}` : name;
    if (
      !error &&
      (listing.directories.includes(path) || listing.files.some((file) => file.path === path))
    )
      error = t("同名文件或文件夹已存在，请使用其他名称。");
    if (error) {
      setCreation({ ...creation, error, focus: ++explorerFocusSequence.current });
      return;
    }
    fileWriteInFlight.current = true;
    setBusy(true);
    setCreating(true);
    try {
      await api("/api/file", { action: creation.action, path });
      setCreation(undefined);
      await reload();
      if (creation.action === "create") await openFile(path, { focus: true });
      else {
        revealDirectory(path);
        focusTreePath(path);
      }
    } catch (error) {
      setCreation({ ...creation, error: String(error), focus: ++explorerFocusSequence.current });
    } finally {
      fileWriteInFlight.current = false;
      setBusy(false);
      setCreating(false);
    }
  };
  const cancelCreation = () => {
    if (creating) return;
    const parent = creation?.parent ?? "";
    setCreation(undefined);
    focusTreePath(parent);
  };
  const collapseExplorer = () => {
    if (busy) return;
    setExpanded(new Set());
    setRootOpen(false);
    setTreeFocusRequest(undefined);
    // A nonempty draft survives hiding the tree and can be resumed by reopening its parent.
    focusTreePath("");
  };
  const startForm = (action: "rename" | "delete", path = selected) => {
    setFormError("");
    setForm({
      action,
      path,
      value: path,
    });
  };
  const newUntitled = () => {
    const number = ++untitledSequence.current;
    const path = `\0untitled:${number}`;
    setDocuments((previous) => [
      ...previous,
      { path, untitled: t("未命名-{0}", { 0: number }), saved: "", content: "", version: "" },
    ]);
    setSourceFocus({ path, groupId: focusedGroupRef.current, origin: document.activeElement });
    if (focusedGroupRef.current === primaryEditorGroup) {
      setSettingsActive(false);
      setSearchEditorActive(false);
      setDiffActive(false);
      setDocumentActive(true);
    }
    setActivePath(path);
    setHistory((previous) => visitDocument(previous, path));
  };
  useEffect(() => {
    if (!editorMenuRequest) return;
    const { id } = editorMenuRequest;
    if (closeDisabled) return;
    if (id === "help.shortcuts") setShowShortcuts(true);
    else if (id === "file.new") startCreation("create");
    else if (id === "file.untitled") newUntitled();
    else if (id === "file.saveAll" && canSaveAll) void saveAll();
    else if (id === "editor.gotoLine" && canReadSource)
      setLinePrompt({ path: active.path, input: "" });
    else if (id === "file.saveAs" && canSaveAs) startSaveAs(active.path);
    else if (id === "file.revert" && canRevert) {
      if (active.content === active.saved) void rereadFile(active.path);
      else setRevertPath(active.path);
    } else if (
      canReadSource &&
      isSourceEditCommand(id) &&
      (canEditSource ||
        ["editor.find", "editor.selectAll", "editor.nextMatch", "editor.allMatches"].includes(id))
    ) {
      runSourceEditCommand(active.path, sourceModels, id, focusedGroup);
    }
  }, [editorMenuRequest]);
  const explorerActions = (path: string): NavigatorMenuItem[] => [
    {
      label: t("新建文件…"),
      icon: "new-file",
      run: busy ? undefined : () => startCreation("create", path),
    },
    {
      label: t("新建文件夹…"),
      icon: "new-folder",
      run: busy ? undefined : () => startCreation("mkdir", path),
    },
    { label: t("刷新"), icon: "refresh", run: busy ? undefined : () => void reload() },
    {
      label: t("折叠所有文件夹"),
      icon: "collapse",
      run: busy ? undefined : collapseExplorer,
    },
    {
      label: t("重命名…"),
      icon: "edit",
      shortcut: ["F2"],
      separator: true,
      run: busy || !path ? undefined : () => startForm("rename", path),
    },
    {
      label: t("删除…"),
      icon: "trash",
      run: busy || !path ? undefined : () => startForm("delete", path),
    },
  ];
  useEffect(() => {
    setExplorerMenu(undefined);
    setHeadingMenu(undefined);
  }, [activity, layout.sidebarOpen]);
  const openExplorerHeadingMenu = (
    heading: ExplorerHeading,
    event: React.MouseEvent<HTMLElement> | React.KeyboardEvent<HTMLElement>,
  ) => {
    const keyboard = "key" in event;
    if (keyboard && event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
    event.preventDefault();
    event.stopPropagation();
    const button =
      event.target instanceof Element ? event.target.closest<HTMLButtonElement>("button") : null;
    const anchor =
      button && !button.disabled
        ? button
        : heading === "panel"
          ? event.currentTarget
          : (event.currentTarget.querySelector("button") ?? event.currentTarget);
    anchor.focus({ preventScroll: true });
    const { left, bottom } = anchor.getBoundingClientRect();
    setHeadingMenu({
      heading,
      x: keyboard ? left : event.clientX,
      y: keyboard ? bottom : event.clientY,
      anchor,
    });
  };
  const explorerHeadingEvents = (heading: ExplorerHeading): React.HTMLAttributes<HTMLElement> => ({
    onContextMenu: (event) => openExplorerHeadingMenu(heading, event),
    onKeyDown: (event) => openExplorerHeadingMenu(heading, event),
  });
  const requiredFolders = t("文件夹是必需的视图。");
  const toggleOpenEditors = () =>
    changeLayout({ explorerOpenEditors: !layout.explorerOpenEditors });
  const explorerViewActions: NavigatorMenuItem[] = [
    {
      label: t("打开的编辑器"),
      checkbox: true,
      checked: !!layout.explorerOpenEditors,
      run: toggleOpenEditors,
    },
    { label: t("文件夹"), checkbox: true, checked: true, reason: requiredFolders },
  ];
  const changeExplorerLayout = (patch: Partial<StudioLayout>) => {
    if (patch.sidebarOpen === false) {
      const sidebar = workspaceToggleRef.current?.closest(".workbench-navigator");
      if (document.activeElement === document.body || sidebar?.contains(document.activeElement)) {
        sidebar
          ?.closest(".workbench-side")
          ?.querySelector<HTMLElement>('.activity-bar[tabindex="0"], .activity-bar [tabindex="0"]')
          ?.focus({ preventScroll: true });
      }
    }
    changeLayout(patch);
  };
  const explorerTitleActions: NavigatorMenuItem[] = [
    ...explorerViewActions,
    { ...activityBarPositionMenuItem(layout, changeLayout), separator: true },
    ...sidebarLayoutMenuItems(layout, changeExplorerLayout),
  ];
  const explorerSectionActions = (heading: "workspace" | "editors"): NavigatorMenuItem[] => [
    heading === "workspace"
      ? { label: t("隐藏“文件夹”"), reason: requiredFolders }
      : { label: t("隐藏“打开的编辑器”"), run: toggleOpenEditors },
    ...explorerViewActions.map((item, index) =>
      index === 0 ? { ...item, separator: true } : item,
    ),
  ];
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
      <NavigatorMenu key="explorer" label={t("资源管理器更多操作")} items={explorerTitleActions} />
    ) : activity === "scm" ? (
      <NavigatorMenu
        key="scm"
        label={t("源码管理范围")}
        title={gitScope === "project" ? state.project.path : gitStatus?.repository}
        triggerClassName="scm-scope-trigger"
        triggerIcon={
          <>
            <span>{t(gitScope === "project" ? "当前项目" : "整个仓库")}</span>
            <Icon name="scm-chevron" size={13} />
          </>
        }
        items={[
          {
            label: t("当前项目"),
            checked: gitScope === "project",
            reason: state.project.path,
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
    ) : activity === "library" ? (
      <>
        <Tool label={t("添加库目录…")} icon="new-folder" onClick={() => setLibraryDialog(true)} />
        <Tool
          label={t("刷新部件库")}
          icon="refresh"
          onClick={() => setLibraryRevision((value) => value + 1)}
        />
      </>
    ) : null;
  const sidebar = (domainTabs: EditorGroupTab[]) => (
    <NavigatorPanel
      className="core-sidebar"
      data-activity={activity}
      title={t(coreSidebarTitles[activity])}
      actions={sidebarActions}
      headingProps={
        activity === "explorer" ? { ...explorerHeadingEvents("panel"), tabIndex: -1 } : undefined
      }
    >
      <NavigatorPage
        hidden={activity !== "explorer"}
        aria-label={t("文件资源管理器内容")}
        onPointerDown={(event) => {
          if (!creation || creating || event.button !== 0) return;
          const target = event.target;
          if (
            target === event.currentTarget ||
            target === explorerRef.current ||
            (target instanceof HTMLElement && target.classList.contains("core-file-tree"))
          ) {
            event.preventDefault();
            cancelCreation();
          }
        }}
      >
        {layout.explorerOpenEditors && (
          <>
            <div className="core-open-editors-heading" {...explorerHeadingEvents("editors")}>
              <button
                type="button"
                className="core-open-editors-toggle"
                aria-expanded={openEditorsExpanded}
                aria-controls={openEditorsId}
                onClick={() => setOpenEditorsExpanded(!openEditorsExpanded)}
              >
                <span className="core-tree-twistie" aria-hidden="true">
                  <Icon name="chevron" size={12} expanded={openEditorsExpanded} />
                </span>
                <span>{t("打开的编辑器")}</span>
              </button>
            </div>
            <div id={openEditorsId} className="core-open-editors" hidden={!openEditorsExpanded}>
              {renderOpenedEditors(domainTabs)}
            </div>
          </>
        )}
        <div className="core-workspace-heading" {...explorerHeadingEvents("workspace")}>
          <button
            ref={workspaceToggleRef}
            className="core-workspace-toggle"
            aria-expanded={workspaceOpen}
            aria-controls={workspaceTreeId}
            onClick={() => setWorkspaceOpen(!workspaceOpen)}
          >
            <span className="core-tree-twistie" aria-hidden="true">
              <Icon name="chevron" size={12} expanded={workspaceOpen} />
            </span>
            <span>{t("工作区")}</span>
          </button>
          {workspaceOpen && (
            <NavigatorToolbar
              className="core-workspace-tools"
              role="toolbar"
              aria-label={t("项目文件操作")}
            >
              <Tool
                label={t("新建文件")}
                icon="new-file"
                onClick={() => startCreation("create")}
                disabled={busy}
              />
              <Tool
                label={t("新建文件夹")}
                icon="new-folder"
                onClick={() => startCreation("mkdir")}
                disabled={busy}
              />
              <Tool
                label={t("刷新资源")}
                icon="refresh"
                onClick={() => void reload()}
                disabled={busy}
              />
              <Tool
                label={t("全部折叠")}
                icon="collapse"
                onClick={collapseExplorer}
                disabled={busy}
              />
            </NavigatorToolbar>
          )}
        </div>
        <div id={workspaceTreeId} ref={explorerRef} hidden={!workspaceOpen}>
          {listingError && (
            <p className="core-error" role="alert">
              {listingError}
            </p>
          )}
          {loadingFiles && !listing.files.length && (
            <p className="core-hint">{t("正在读取文件…")}</p>
          )}
          <ExplorerTree
            entries={tree}
            root={{
              name: state.project.name,
              expanded: rootOpen,
              toggle: () => setRootOpen(!rootOpen),
              ref: projectRootRef,
            }}
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
            open={(path, keyboard) => {
              const definition = moduleDirectories.get(path);
              if (definition) openDesign({ definition }, "model");
              else void openFile(path, { focus: keyboard });
            }}
            contextMenu={(path, x, y) => setExplorerMenu({ path, x, y })}
            rename={() => startForm("rename")}
            remove={() => startForm("delete")}
            creation={creation}
            creating={creating}
            changeCreation={(value) =>
              setCreation((previous) => previous && { ...previous, value, error: undefined })
            }
            submitCreation={() => void submitCreation()}
            cancelCreation={cancelCreation}
            focusRequest={treeFocusRequest}
            focusHandled={() => setTreeFocusRequest(undefined)}
          />
          {listing.truncated && (
            <p className="core-hint">{t("文件列表达到上限，部分文件未显示。")}</p>
          )}
          {!loadingFiles && !listing.files.length && !listing.directories.length && (
            <p className="core-hint">{t("项目中尚无文件，可在上方新建。")}</p>
          )}
        </div>
      </NavigatorPage>
      <NavigatorPage
        className="core-search"
        hidden={activity !== "search"}
        aria-label={t("全文搜索内容")}
      >
        {codeSearch.sidebar}
      </NavigatorPage>
      <NavigatorPage
        className="core-scm"
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
              scope={gitScope}
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
      </NavigatorPage>
      <NavigatorPage hidden={activity !== "extensions"} aria-label={t("已加载的扩展")}>
        {state.plugins.length ? (
          <ul className="core-activity-list">
            {state.plugins.map((plugin) => (
              <li className="core-activity-row" key={plugin.id}>
                <Icon name="extensions" />
                <span className="core-activity-name">
                  <strong>{plugin.name}</strong>
                  <small>
                    {t(plugin.discipline)} · {plugin.version}
                  </small>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="core-hint">{t("暂无已加载的扩展。")}</p>
        )}
      </NavigatorPage>
      {(["library", "instances"] as const).map((mode) => (
        <NavigatorPage key={mode} hidden={activity !== mode}>
          {designError ? (
            <p className="core-error" role="alert">
              {designError}
            </p>
          ) : designIndex ? (
            <DesignExplorer
              mode={mode}
              index={designIndex}
              open={openDesign}
              editTags={() => void openFile("texsys.toml")}
            />
          ) : (
            <p className="core-hint">{t("读取中")}</p>
          )}
        </NavigatorPage>
      ))}
      {headingMenu && activity === "explorer" && layout.sidebarOpen && (
        <NavigatorMenu
          label={t(
            headingMenu.heading === "panel"
              ? "资源管理器更多操作"
              : headingMenu.heading === "workspace"
                ? "工作区视图操作"
                : "打开的编辑器视图操作",
          )}
          point={headingMenu}
          items={
            headingMenu.heading === "panel"
              ? explorerTitleActions
              : explorerSectionActions(headingMenu.heading)
          }
          onDismiss={() => {
            setHeadingMenu(undefined);
            if (document.activeElement !== document.body) return;
            const anchor = headingMenu.anchor.isConnected
              ? headingMenu.anchor
              : workspaceToggleRef.current;
            if (!anchor?.closest("[hidden], [inert]")) anchor?.focus({ preventScroll: true });
          }}
        />
      )}
    </NavigatorPanel>
  );
  const fileDialogTitle =
    form &&
    t(
      {
        rename: "重命名",
        delete: "删除文件",
        saveAs: "另存为",
      }[form.action],
    );
  const closingDocument = documents.find((item) => item.path === closePath);
  const dialogs = (
    <>
      {libraryDialog && (
        <LibraryFolders
          folders={libraryFolders}
          sources={designIndex?.sources ?? []}
          add={addLibraryFolder}
          remove={(path) => changeLibraryFolders(libraryFolders.filter((item) => item !== path))}
          close={() => setLibraryDialog(false)}
        />
      )}
      {showShortcuts && (
        <div className="core-dialog-backdrop">
          <section
            className="core-dialog core-shortcut-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={t("键盘快捷键参考")}
          >
            <div className="core-shortcut-heading">
              <strong>{t("键盘快捷键参考")}</strong>
              <button onClick={() => setShowShortcuts(false)}>{t("关闭")}</button>
            </div>
            <table>
              <thead>
                <tr>
                  <th scope="col">{t("命令")}</th>
                  <th scope="col">{t("快捷键")}</th>
                </tr>
              </thead>
              <tbody>
                {shortcutCommands.map((item) => (
                  <tr key={item.id}>
                    <td>
                      {item.group} · {item.label}
                    </td>
                    <td>
                      <Keycaps keys={item.keys ?? []} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>
      )}
      {linePrompt && (
        <div className="core-dialog-backdrop">
          <form
            className="core-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={t("转到行／列")}
            onSubmit={(event) => {
              event.preventDefault();
              if (goToSourcePosition(linePrompt.path, sourceModels, linePrompt.input, focusedGroup))
                setLinePrompt(undefined);
              else setLinePrompt({ ...linePrompt, invalid: true });
            }}
          >
            <strong>{t("转到行／列")}</strong>
            <label>
              {t("行号或行号:列号")}
              <input
                value={linePrompt.input}
                aria-invalid={linePrompt.invalid}
                onChange={(event) =>
                  setLinePrompt({ path: linePrompt.path, input: event.target.value })
                }
              />
            </label>
            {linePrompt.invalid && (
              <p className="core-error" role="alert">
                {t("请输入文件范围内的行号和列号。")}
              </p>
            )}
            <div className="core-dialog-actions">
              <button type="button" onClick={() => setLinePrompt(undefined)}>
                {t("取消")}
              </button>
              <button className="core-primary" disabled={!linePrompt.input.trim()}>
                {t("转到")}
              </button>
            </div>
          </form>
        </div>
      )}
      {form && ["rename", "delete", "saveAs"].includes(form.action) && (
        <div className="core-dialog-backdrop">
          <form
            className="core-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={fileDialogTitle}
            onSubmit={(event) => void submitFileOperation(event)}
          >
            <strong>{fileDialogTitle}</strong>
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
              <button
                type="button"
                onClick={() => {
                  setForm(undefined);
                  if (form.closeAfter) finishPendingClose(false);
                }}
                disabled={busy}
              >
                {t("取消")}
              </button>
              <button
                className="core-primary"
                disabled={busy || (form.action !== "delete" && !form.value.trim())}
              >
                {form.action === "delete"
                  ? t("删除")
                  : form.action === "saveAs"
                    ? t("保存")
                    : t("确定")}
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
              {closingDocument?.untitled ?? closePath}
              {t("有未保存的编辑。")}
            </p>
            <div className="core-dialog-actions">
              <button disabled={busy} onClick={() => finishPendingClose(false)}>
                {t("取消")}
              </button>
              <button disabled={busy} onClick={() => finishPendingClose(true)}>
                {t("放弃更改")}
              </button>
              <button
                className="core-primary"
                disabled={busy}
                onClick={async () => {
                  if (closingDocument?.untitled) {
                    setClosePath(undefined);
                    startSaveAs(closePath, true);
                  } else if (await saveFile(closePath)) {
                    const current = documentsRef.current.find((item) => item.path === closePath);
                    if (current?.content === current?.saved) finishPendingClose(true);
                  }
                }}
              >
                {t("保存并关闭")}
              </button>
            </div>
          </div>
        </div>
      )}
      {revertPath && (
        <div className="core-dialog-backdrop">
          <div className="core-dialog" role="dialog" aria-modal="true" aria-label={t("还原文件")}>
            <strong>{t("还原文件")}</strong>
            <p>{t("放弃“{0}”未保存的更改，并重新读取磁盘上的内容？", { 0: revertPath })}</p>
            <div className="core-dialog-actions">
              <button disabled={busy} onClick={() => setRevertPath(undefined)}>
                {t("取消")}
              </button>
              <button
                className="core-primary"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await rereadFile(revertPath);
                  } finally {
                    setRevertPath(undefined);
                    setBusy(false);
                  }
                }}
              >
                {t("还原")}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
  const renderTabs = (group: EditorGroup) => {
    const activePath = group.activePath;
    const activeTab =
      group.id === primaryEditorGroup
        ? settingsActive
          ? "settings"
          : !documentActive
            ? null
            : searchEditorActive
              ? "search"
              : diffActive
                ? "diff"
                : "source"
        : "source";
    const tabStop = activeTab;
    return (
      <div
        className="core-file-tabs"
        role="tablist"
        aria-label={t("工作区文档")}
        onKeyDown={(event) => {
          if (event.defaultPrevented || event.nativeEvent.isComposing || event.keyCode === 229)
            return;
          if (event.altKey || event.metaKey || event.ctrlKey || event.shiftKey) return;
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
        {documents
          .filter((document) => group.views.some((view) => view.path === document.path))
          .map((document) => {
            const selection = document.design?.selection;
            const scope = selection && designIndex && resolveDesign(designIndex, selection);
            const module = designIndex?.library.some(
              (item) => item.kind === "modules" && item.definition === scope?.key,
            );
            const title = selection
              ? (("occurrence" in selection ? selection.occurrence.at(-1) : undefined) ??
                scope?.definition?.name ??
                t("模型"))
              : (document.untitled ??
                fileDocumentTitle(
                  document.path,
                  documents.filter((item) => !item.design).map((item) => item.path),
                ));
            const preview =
              resolvedEditorPresentation(
                filePreviewKind(document),
                group.views.find((view) => view.path === document.path)?.presentation,
              ) === "preview";
            return (
              <DocumentTab
                key={document.path}
                selected={activeTab === "source" && activePath === document.path}
                tabStop={tabStop === "source" && activePath === document.path}
                title={selection || document.untitled ? title : document.path}
                closeLabel={t("关闭 {0}", {
                  "0": selection ? (title ?? "") : (document.untitled ?? document.path),
                })}
                closeDisabled={closeDisabled}
                closeShortcut={closeShortcut}
                onClose={() => closeFileTab(document.path, group.id)}
                onSelect={() => {
                  focusGroup(group.id);
                  if (
                    !document.untitled &&
                    !document.design &&
                    !document.loading &&
                    !document.error
                  )
                    rememberRecentFile(state.project.path, document.path);
                  if (group.id === primaryEditorGroup) {
                    setSettingsActive(false);
                    setSearchEditorActive(false);
                    setDocumentActive(true);
                    setDiffActive(false);
                  }
                  setHistory((previous) => visitDocument(previous, document.path));
                  setActivePath(document.path, group.id);
                }}
              >
                {selection ? (
                  <Icon name={module ? "module" : "system"} size={14} />
                ) : preview ? (
                  <Icon name="preview" size={14} />
                ) : (
                  <FileIcon path={document.path} />
                )}
                <span>{preview ? t("预览 {0}", { 0: title }) : title}</span>
                {document.content !== document.saved && (
                  <span className="core-dirty" title={t("未保存")} />
                )}
              </DocumentTab>
            );
          })}
        {group.id === primaryEditorGroup && searchDocumentOpen && (
          <DocumentTab
            selected={activeTab === "search"}
            tabStop={tabStop === "search"}
            closeLabel={t("关闭搜索结果")}
            closeDisabled={closeDisabled}
            closeShortcut={closeShortcut}
            onClose={closeSearchTab}
            onSelect={() => {
              setSettingsActive(false);
              setSearchEditorActive(true);
              setDocumentActive(true);
              setDiffActive(false);
            }}
          >
            <Icon name="search" size={14} />
            <span>{t("搜索结果")}</span>
          </DocumentTab>
        )}
        {group.id === primaryEditorGroup && diffActive && (
          <DocumentTab
            selected={activeTab === "diff"}
            closeLabel={t("关闭差异")}
            closeDisabled={closeDisabled}
            closeShortcut={closeShortcut}
            onClose={closeDiffTab}
            onSelect={() => {
              setSettingsActive(false);
              setDocumentActive(true);
              setSearchEditorActive(false);
            }}
          >
            <span>
              {t("差异 ·")}
              {diff?.path.split("/").pop() ?? t("读取中")}
            </span>
          </DocumentTab>
        )}
        {group.id === primaryEditorGroup && settingsOpen && (
          <DocumentTab
            selected={activeTab === "settings"}
            tabStop={tabStop === "settings"}
            closeLabel={t("关闭设置")}
            closeDisabled={closeDisabled}
            closeShortcut={closeShortcut}
            onClose={closeSettingsTab}
            onSelect={() => setSettingsActive(true)}
          >
            <Icon name="settings" size={14} />
            <span>{t("设置")}</span>
          </DocumentTab>
        )}
      </div>
    );
  };
  const diffTitle = diff?.historical
    ? t("提交 {0} · 所选范围差异", { 0: diff.path })
    : (diff?.title ?? (diff?.staged ? t("已暂存 ↔ HEAD") : t("工作区 ↔ 暂存区")));
  const diffProjectPath =
    diff && !diff.historical ? gitPathInProject(diff.path, gitStatus?.project_prefix) : undefined;
  const changeDocumentContent = (path: string, content: string) => {
    setEditorGroups((groups) =>
      groups.map((group) => ({
        ...group,
        views: group.views.map((view) =>
          view.path === path && view.location ? { ...view, location: undefined } : view,
        ),
      })),
    );
    setDocuments((previous) =>
      previous.map((item) =>
        item.path === path && item.content !== content
          ? { ...item, content, location: undefined }
          : item,
      ),
    );
  };
  const splitFile = (group: EditorGroup, presentation: EditorPresentation) => {
    if (!group.activePath) return;
    const target = group.id === primaryEditorGroup ? secondaryEditorGroup : primaryEditorGroup;
    setEditorGroups((groups) => openEditorView(groups, target, group.activePath, { presentation }));
    if (target === primaryEditorGroup) {
      setSettingsActive(false);
      setSearchEditorActive(false);
      setDiffActive(false);
      setDocumentActive(true);
    }
    focusGroup(target);
  };
  const groupTabs = (group: EditorGroup, domainTabs: EditorGroupTab[] = []): EditorGroupTab[] => {
    const primary = group.id === primaryEditorGroup;
    const tabs: EditorGroupTab[] = documents
      .filter((document) => group.views.some((view) => view.path === document.path))
      .map((document) => {
        const selection = document.design?.selection;
        const scope = selection && designIndex && resolveDesign(designIndex, selection);
        const label = selection
          ? (("occurrence" in selection ? selection.occurrence.at(-1) : undefined) ??
            scope?.definition?.name ??
            t("模型"))
          : (document.untitled ??
            fileDocumentTitle(
              document.path,
              documents.filter((item) => !item.design).map((item) => item.path),
            ));
        const preview =
          resolvedEditorPresentation(
            filePreviewKind(document),
            group.views.find((view) => view.path === document.path)?.presentation,
          ) === "preview";
        return {
          id: `file:${document.path}`,
          label: preview ? t("预览 {0}", { 0: label }) : label,
          description: document.untitled ?? (selection ? "" : document.path),
          icon: selection ? "system" : preview ? "preview" : "file",
          dirty: !!document.untitled || document.content !== document.saved,
          selected:
            group.activePath === document.path &&
            (!primary || (documentActive && !settingsActive && !searchEditorActive && !diffActive)),
          select: () => {
            focusGroup(group.id);
            if (primary) {
              setSettingsActive(false);
              setSearchEditorActive(false);
              setDiffActive(false);
              setDocumentActive(true);
            }
            setActivePath(document.path, group.id);
            setHistory((previous) => visitDocument(previous, document.path));
            setCloseFocusRequest((request) => request + 1);
          },
          close: () => closeFileTab(document.path, group.id),
        };
      });
    if (!primary) return tabs;
    if (searchDocumentOpen)
      tabs.push({
        id: "search",
        label: t("搜索结果"),
        icon: "search",
        selected: searchEditorActive && !settingsActive,
        select: () => {
          focusGroup(primaryEditorGroup);
          setDocumentActive(true);
          setSettingsActive(false);
          setDiffActive(false);
          setSearchEditorActive(true);
        },
        close: () => {
          closeSearchTab(false);
          return true;
        },
      });
    if (diffActive)
      tabs.push({
        id: "diff",
        label: `${t("差异 ·")}${diff?.path.split("/").pop() ?? t("读取中")}`,
        icon: "branch",
        selected: !settingsActive && !searchEditorActive,
        select: () => {
          focusGroup(primaryEditorGroup);
          setDocumentActive(true);
          setSettingsActive(false);
          setSearchEditorActive(false);
        },
        close: () => {
          closeDiffTab(false);
          return true;
        },
      });
    if (settingsOpen)
      tabs.push({
        id: "settings",
        label: t("设置"),
        icon: "settings",
        selected: settingsActive,
        select: () => {
          focusGroup(primaryEditorGroup);
          setSettingsActive(true);
        },
        close: () => {
          closeSettingsTab(false);
          return true;
        },
      });
    return [
      ...tabs,
      ...domainTabs.map((tab) => ({
        ...tab,
        id: `domain:${tab.id}`,
        selected: !documentActive && !settingsActive && tab.selected,
        select: () => {
          activateDomain();
          tab.select();
        },
      })),
    ];
  };
  const renderOpenedEditors = (domainTabs: EditorGroupTab[]) => {
    const groups = editorGroups.map((group) => ({
      id: group.id,
      tabs: groupTabs(group, domainTabs),
    }));
    if (groups.every(({ tabs }) => !tabs.length)) {
      return <p className="core-hint">{t("没有打开的编辑器。")}</p>;
    }
    return groups.map(({ id, tabs }, index) =>
      tabs.length ? (
        <ul
          className="core-open-editor-list"
          key={id}
          aria-label={groups.length > 1 ? t("组 {0}", { 0: index + 1 }) : t("打开的编辑器")}
        >
          {tabs.map((tab) => (
            <li key={tab.id}>
              <button
                type="button"
                className="core-open-editor-row"
                title={tab.description || tab.label}
                aria-current={tab.selected ? "true" : undefined}
                disabled={closeDisabled}
                onClick={tab.select}
              >
                <Icon name={tab.icon ?? "file"} size={14} />
                <span className="core-open-editor-label">{tab.label}</span>
                {tab.dirty && <span className="core-dirty" role="img" aria-label={t("未保存")} />}
              </button>
            </li>
          ))}
        </ul>
      ) : null,
    );
  };
  const closeGroupTabs = async (group: EditorGroup, tabs: EditorGroupTab[], savedOnly: boolean) => {
    if (closeDisabled) return;
    focusGroup(group.id);
    setClosingGroup(true);
    const remaining = new Map(tabs.map((tab) => [tab.id, tab]));
    try {
      await closeEditorsInOrder(
        (savedOnly ? tabs.filter((tab) => !tab.dirty) : tabs).map((tab) => ({
          ...tab,
          close: async () => {
            const closed = await tab.close();
            if (closed) remaining.delete(tab.id);
            return closed;
          },
        })),
      );
    } finally {
      setClosingGroup(false);
      if (group.id === primaryEditorGroup && !groupsRef.current[0].views.length) {
        const survivors = [...remaining.values()];
        if (survivors.length && survivors.every((tab) => tab.id.startsWith("domain:"))) {
          (survivors.find((tab) => tab.selected) ?? survivors[0]).select();
          setCloseFocusRequest((request) => request + 1);
        } else if (!survivors.length) {
          setDocumentActive(true);
          if (groupsRef.current.some((item) => item.id === secondaryEditorGroup)) {
            focusGroup(secondaryEditorGroup);
            setCloseFocusRequest((request) => request + 1);
          }
        }
      }
    }
  };
  const renderActions = (group: EditorGroup, domainTabs: EditorGroupTab[] = []) => {
    const tabs = groupTabs(group, domainTabs);
    const items: NavigatorMenuItem[] = [
      {
        label: t("显示打开的编辑器"),
        run:
          tabs.length && !closeDisabled
            ? () =>
                showEditorPicker(
                  tabs.map((tab, index) => ({
                    id: `studio.editor:${group.id}:${index}`,
                    label: tab.label,
                    group: [tab.description, tab.dirty ? t("未保存") : ""]
                      .filter(Boolean)
                      .join(" · "),
                    icon: tab.icon,
                    checked: !!tab.selected,
                    keywords: tab.description,
                    run: tab.select,
                  })),
                )
            : undefined,
      },
      {
        label: t("全部关闭"),
        run:
          tabs.length && !closeDisabled ? () => void closeGroupTabs(group, tabs, false) : undefined,
      },
      {
        label: t("关闭已保存"),
        run:
          tabs.some((tab) => !tab.dirty) && !closeDisabled
            ? () => void closeGroupTabs(group, tabs, true)
            : undefined,
      },
      {
        label: t("配置编辑器"),
        separator: true,
        run: !closeDisabled
          ? () => {
              focusGroup(primaryEditorGroup);
              setSettingsOpen(true);
              setSettingsActive(true);
              setSettingsCategoryRequest((previous) => ({
                category: "editor",
                token: (previous?.token ?? 0) + 1,
              }));
            }
          : undefined,
      },
    ];
    const more = (
      <NavigatorMenu
        label={t("更多操作")}
        items={items}
        triggerClassName="file-editor-more-action"
      />
    );
    if (group.id === primaryEditorGroup) {
      if (settingsActive || !documentActive) return more;
      if (searchEditorActive)
        return (
          <>
            {codeSearch.editorActions}
            {more}
          </>
        );
      if (diffActive)
        return (
          <>
            {diffProjectPath !== undefined && (
              <EditorActionButton
                label={t("打开文件")}
                icon="scm-open-file"
                onClick={() => void openFile(diffProjectPath, { groupId: group.id })}
              />
            )}
            {more}
          </>
        );
    }
    const active = documents.find((item) => item.path === group.activePath);
    if (!active || active.design || active.loading || active.error) return more;
    const kind = filePreviewKind(active);
    const view = group.views.find((item) => item.path === active.path);
    const presentation = resolvedEditorPresentation(kind, view?.presentation);
    return (
      <FileEditorActions
        kind={kind}
        presentation={presentation}
        onPreview={() =>
          updateView(group.id, active.path, {
            presentation: presentation === "preview" ? "source" : "preview",
          })
        }
        onPreviewBeside={() => splitFile(group, "preview")}
        onSplit={() => splitFile(group, presentation)}
        items={items}
        disabled={closeDisabled}
      />
    );
  };
  const renderEditorContent = (group: EditorGroup) => {
    const active = documents.find((item) => item.path === group.activePath);
    const view = group.views.find((item) => item.path === group.activePath);
    const previewKind = filePreviewKind(active);
    const presentation = resolvedEditorPresentation(previewKind, view?.presentation);
    const showingPreview = presentation === "preview";
    const activeDirty = !!active && active.content !== active.saved;
    const primary = group.id === primaryEditorGroup;
    return primary && searchEditorActive ? (
      codeSearch.editor
    ) : primary && diffActive ? (
      <section className="core-diff-document" aria-label={t("Git 差异")}>
        <div className="core-document-header">
          <span>
            {diff?.path} {diff ? `· ${diffTitle}` : ""}
          </span>
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
        <Icon name="system" size={44} />
        <h2>{t("打开文件，开始编辑")}</h2>
        <p>{t("从文件资源管理器选择文件，或使用 ⌘ / Ctrl + P 快速打开。")}</p>
        <div className="core-welcome-shortcuts">
          <div className="core-welcome-shortcut">
            <span>{t("命令面板…")}</span>
            <Keycaps keys={macKeys ? ["⇧", "⌘", "P"] : ["Ctrl", "Shift", "P"]} />
          </div>
          <div className="core-welcome-shortcut">
            <span>{t("搜索文件…")}</span>
            <Keycaps keys={macKeys ? ["⌘", "P"] : ["Ctrl", "P"]} />
          </div>
          <div className="core-welcome-shortcut">
            <span>{t("搜索项目内容")}</span>
            <Keycaps keys={macKeys ? ["⇧", "⌘", "F"] : ["Ctrl", "Shift", "F"]} />
          </div>
        </div>
      </div>
    ) : active.design ? (
      designIndex ? (
        <DesignDocument
          index={designIndex}
          selection={active.design.selection}
          openFile={(path) => void openFile(path, { focus: true, groupId: group.id })}
          referenceEntry={referenceLibrary}
        />
      ) : (
        <Empty title={t("无法解析设计对象")}>{designError}</Empty>
      )
    ) : active.loading ? (
      <Empty title={t("正在打开文件")}>{active.path}</Empty>
    ) : active.error ? (
      <Empty
        title={t("文件读取失败")}
        action={
          <EditorActionButton
            label={t("重新读取")}
            icon="refresh"
            onClick={() => void rereadFile(active.path)}
          />
        }
      >
        {t(active.error ?? active.reason)}
      </Empty>
    ) : (
      <section
        className="core-text-document"
        aria-label={t("编辑 {0}", {
          "0": active.untitled ?? active.path,
        })}
      >
        <div className="core-document-header">
          <nav
            className="core-breadcrumb"
            aria-label={t("文件路径")}
            title={active.untitled ?? active.path}
          >
            {(active.untitled ?? active.path).split("/").map((part, index, parts) => (
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
                      revealDirectory(path);
                      focusTreePath(path);
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
          <FileOpenWith
            path={active.path}
            kind={previewKind}
            presentation={presentation}
            defaultPresentation={fileEditorDefault(active.path, previewKind, fileAssociations)}
            onSetDefault={(mode) => {
              setFileEditorDefault(active.path, previewKind, mode);
            }}
            onChange={(presentation) => updateView(group.id, active.path, { presentation })}
          />
        </div>
        {showingPreview && previewKind ? (
          <FilePreviewContent
            key={active.path}
            api={api}
            path={active.path}
            kind={previewKind}
            content={active.content}
            version={active.version}
            preview={active.preview}
            dirty={activeDirty}
            openFile={(path) => void openFile(path, { groupId: group.id })}
            openSource={(source) =>
              void openFile(source.file, {
                focus: true,
                groupId: group.id,
                presentation: "source",
                location: source.line
                  ? { line: source.line, column: source.column ?? 1 }
                  : undefined,
              })
            }
          />
        ) : active.reason ? (
          <Empty title={t("暂不支持显示源代码")}>{t(active.reason)}</Empty>
        ) : presentation === "markdown-editor" && previewKind === "markdown" ? (
          <MarkdownEditor
            path={active.path}
            value={active.content}
            onChange={(content) => changeDocumentContent(active.path, content)}
            onFocus={() => focusGroup(group.id)}
          />
        ) : (
          <>
            <SourceEditor
              path={active.path}
              viewId={group.id}
              onFocus={() => focusGroup(group.id)}
              value={active.content}
              models={sourceModels}
              visible={!primary || (documentActive && !settingsActive)}
              location={view?.location}
              focusRequest={
                sourceFocus?.path === active.path && sourceFocus.groupId === group.id
                  ? sourceFocus
                  : undefined
              }
              onFocusHandled={() => setSourceFocus(undefined)}
              onChange={(content) => changeDocumentContent(active.path, content)}
              onStatus={(status) => {
                sourceStatuses.current.set(group.id, { path: active.path, status });
                if (group.id === activeGroup.id && active.path === sourceStatusPath)
                  onSourceStatus?.(status);
              }}
            />
          </>
        )}
      </section>
    );
  };
  const inspector = (
    <section className="core-file-properties">
      <div className="core-heading">
        <strong>{t("文件属性")}</strong>
      </div>
      {active && !active.design && !diffActive && !searchEditorActive ? (
        <dl>
          <dt>{t("路径")}</dt>
          <dd>{active.untitled ?? active.path}</dd>
          <dt>{t("编码")}</dt>
          <dd>UTF-8</dd>
          <dt>{t("状态")}</dt>
          <dd>
            {active.loading
              ? t("读取中")
              : active.reason
                ? t("不可编辑")
                : active.untitled || active.content !== active.saved
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
  const primaryGroup = editorGroups[0];
  const secondaryGroup = editorGroups.find((group) => group.id === secondaryEditorGroup);
  const value: Core = {
    activity,
    select,
    sidebar,
    editor: (
      <>
        <div className="core-editor-page" hidden={settingsActive}>
          {renderEditorContent(primaryGroup)}
        </div>
        {settingsOpen && (
          <Settings
            api={api}
            titlebar={titlebarSettings}
            hidden={!settingsActive}
            categoryRequest={settingsCategoryRequest}
          />
        )}
      </>
    ),
    editorActions: (domainTabs) => renderActions(primaryGroup, domainTabs),
    tabs: renderTabs(primaryGroup),
    secondary: secondaryGroup
      ? {
          id: secondaryGroup.id,
          tabs: renderTabs(secondaryGroup),
          editorActions: renderActions(secondaryGroup),
          editor: renderEditorContent(secondaryGroup),
        }
      : undefined,
    focusedGroup,
    focusGroup,
    inspector,
    documentActive: documentActive || settingsActive,
    closeFocusRequest,
    activateDomain,
    activateCore,
    hasCoreTabs,
    setDomainTabsOpen,
    hasDomain,
    changeCount: gitStatus?.entries.length ?? 0,
  };
  return (
    <CoreContext.Provider value={value}>
      {children}
      {codeSearch.dialog}
      {explorerMenu && activity === "explorer" && layout.sidebarOpen && (
        <NavigatorMenu
          label={t("资源管理器操作")}
          point={explorerMenu}
          items={explorerActions(explorerMenu.path)}
          onDismiss={() => setExplorerMenu(undefined)}
        />
      )}
      {(form || closePath || revertPath || linePrompt || showShortcuts || libraryDialog) && (
        <DialogLayer
          cancel={() => {
            if (!busy) {
              setForm(undefined);
              finishPendingClose(false);
              setRevertPath(undefined);
              setLinePrompt(undefined);
              setShowShortcuts(false);
              setLibraryDialog(false);
            }
          }}
        >
          {dialogs}
        </DialogLayer>
      )}
    </CoreContext.Provider>
  );
}

/** Host-owned composition; professional plugins contribute preview surfaces. */
export function WorkbenchSurface({
  layout,
  change,
  inspector,
  bottom,
  tabs,
  tabActions,
  editorTabs = [],
  children,
}: {
  layout: StudioLayout;
  change: WorkbenchProps["changeLayout"];
  inspector?: ReactNode;
  bottom?: ReactNode;
  tabs?: ReactNode;
  tabActions?: ReactNode;
  editorTabs?: EditorGroupTab[];
  children?: ReactNode;
}) {
  const core = useCoreWorkspace();
  const [splitRatio, setSplitRatio] = useState(50);
  if (!core) throw new Error("WorkbenchSurface requires the Studio host.");
  const documentActions = (
    <>
      {!core.documentActive && tabActions}
      {core.editorActions(editorTabs)}
    </>
  );
  const items = coreItems.map(({ id, label, icon, shortcutKey }) => ({
    id,
    label,
    icon: <Icon name={icon} size={19} />,
    count: id === "scm" ? core.changeCount : undefined,
    shortcut: shortcutKey
      ? [
          ...(macKeys ? (id === "explorer" ? ["⇧", "⌘"] : ["⌃", "⇧"]) : ["Ctrl", "Shift"]),
          shortcutKey,
        ]
      : undefined,
  }));
  const hasTabs = core.hasCoreTabs || Boolean(tabs);
  const splitVisible = !!core.secondary && hasTabs;
  return (
    <WorkbenchLayout
      layout={layout}
      change={change}
      activity={
        <ActivityBar
          active={core.activity}
          items={items}
          select={core.select}
          layout={layout}
          change={change}
        />
      }
      sidebar={core.sidebar(editorTabs)}
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
      <div
        className={`core-editor-groups${splitVisible ? " is-split" : ""}`}
        style={
          splitVisible
            ? {
                gridTemplateColumns: `minmax(0, ${splitRatio}fr) 5px minmax(0, ${100 - splitRatio}fr)`,
              }
            : undefined
        }
      >
        {(!core.secondary || hasTabs) && (
          <EditorGroupSurface
            id={primaryEditorGroup}
            focused={core.focusedGroup === primaryEditorGroup}
            focus={() => core.focusGroup(primaryEditorGroup)}
            closeFocusRequest={core.closeFocusRequest}
            hasTabs={hasTabs}
            actions={hasTabs ? documentActions : undefined}
            tabs={
              <>
                {core.tabs}
                {tabs && (
                  <div
                    className={`core-domain-tabs ${core.documentActive ? "inactive" : ""}`}
                    onClick={(event) => {
                      if ((event.target as HTMLElement).closest('[role="tab"]'))
                        core.activateDomain();
                    }}
                  >
                    {tabs}
                  </div>
                )}
              </>
            }
          >
            <div className="core-document-content" hidden={!core.documentActive}>
              {core.editor}
            </div>
            <div className="core-domain-content" hidden={core.documentActive}>
              {children}
            </div>
          </EditorGroupSurface>
        )}
        {core.secondary && (
          <>
            {splitVisible && <EditorGroupDivider value={splitRatio} onChange={setSplitRatio} />}
            <EditorGroupSurface
              id={secondaryEditorGroup}
              focused={core.focusedGroup === secondaryEditorGroup}
              focus={() => core.focusGroup(secondaryEditorGroup)}
              closeFocusRequest={core.closeFocusRequest}
              hasTabs
              tabs={core.secondary.tabs}
              actions={core.secondary.editorActions}
            >
              <div className="core-document-content">{core.secondary.editor}</div>
            </EditorGroupSurface>
          </>
        )}
      </div>
    </WorkbenchLayout>
  );
}

function EditorGroupSurface({
  id,
  focused,
  focus,
  closeFocusRequest,
  hasTabs,
  tabs,
  actions,
  children,
}: {
  id: string;
  focused: boolean;
  focus: () => void;
  closeFocusRequest: number;
  hasTabs: boolean;
  tabs: ReactNode;
  actions: ReactNode;
  children: ReactNode;
}) {
  const tabList = useRef<HTMLDivElement>(null);
  const documentArea = useRef<HTMLDivElement>(null);
  const lastCloseFocus = useRef(closeFocusRequest);
  useLayoutEffect(() => {
    const changed = lastCloseFocus.current !== closeFocusRequest;
    lastCloseFocus.current = closeFocusRequest;
    if (!changed || !focused) return;
    const list = tabList.current;
    if (!list || list.closest("[hidden]")) return;
    const selected = Array.from(
      list.querySelectorAll<HTMLButtonElement>('[role="tab"][aria-selected="true"]'),
    ).find((tab) => !tab.closest(".inactive"));
    (selected ?? documentArea.current)?.focus();
  }, [closeFocusRequest, focused]);
  const lastSelectedTab = useRef<Element | null>(null);
  useLayoutEffect(() => {
    const list = tabList.current;
    if (!list) return;
    const selected = Array.from(list.querySelectorAll('[role="tab"][aria-selected="true"]')).find(
      (tab) => !tab.closest(".inactive"),
    );
    if (selected === lastSelectedTab.current) return;
    lastSelectedTab.current = selected ?? null;
    if (!selected) return;
    const tab = (selected.closest(".core-file-tab, .page-tab") ?? selected).getBoundingClientRect();
    const visible = list.getBoundingClientRect();
    if (tab.left < visible.left) list.scrollLeft += tab.left - visible.left;
    else if (tab.right > visible.right) list.scrollLeft += tab.right - visible.right;
  });
  return (
    <div
      data-editor-group={id}
      className={`core-editor-surface${hasTabs ? "" : " no-tabs"}${focused ? " is-focused" : ""}`}
      onFocusCapture={focus}
      onPointerDownCapture={focus}
    >
      <div className="core-document-tabs">
        <div className="core-document-tab-list" ref={tabList}>
          {tabs}
        </div>
        {actions && (
          <div className="core-document-actions" role="group" aria-label={t("文档操作")}>
            {actions}
          </div>
        )}
      </div>
      <div
        className="core-surface-content"
        ref={documentArea}
        role="group"
        aria-label={t("文档区域")}
        tabIndex={-1}
      >
        {children}
      </div>
    </div>
  );
}

function EditorGroupDivider({
  value,
  onChange,
}: {
  value: number;
  onChange: (value: number) => void;
}) {
  const cleanup = useRef<(() => void) | null>(null);
  useEffect(() => () => cleanup.current?.(), []);
  const clamp = (value: number) => Math.max(20, Math.min(80, value));
  return (
    <div
      className="core-editor-divider"
      role="separator"
      aria-label={t("调整编辑器分栏宽度")}
      aria-orientation="vertical"
      aria-valuemin={20}
      aria-valuemax={80}
      aria-valuenow={Math.round(value)}
      tabIndex={0}
      onDoubleClick={() => onChange(50)}
      onKeyDown={(event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        onChange(clamp(value + (event.key === "ArrowLeft" ? -5 : 5)));
      }}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        cleanup.current?.();
        const rect = event.currentTarget.parentElement!.getBoundingClientRect();
        const start = value;
        const move = (next: PointerEvent) => {
          if (next.pointerId === event.pointerId)
            onChange(clamp((100 * (next.clientX - rect.left)) / rect.width));
        };
        const end = () => {
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", end);
          window.removeEventListener("pointercancel", cancel);
          window.removeEventListener("keydown", key);
          cleanup.current = null;
        };
        const cancel = () => {
          onChange(start);
          end();
        };
        const key = (next: KeyboardEvent) => {
          if (next.key === "Escape") {
            next.preventDefault();
            cancel();
          }
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", end);
        window.addEventListener("pointercancel", cancel);
        window.addEventListener("keydown", key);
        cleanup.current = end;
      }}
    />
  );
}
