import React, { useEffect, useImperativeHandle, useRef, useState } from "react";
import { ActivityBar, WorkbenchLayout } from "./atomstudio/Layout";
import { WorkspaceTabStrip } from "./atomstudio/WorkspaceTabs";
import { WorkbenchBottomPanel } from "./BottomPanel";
import { coreItems, coreSidebarTitles, isCoreActivity, type Activity } from "./CoreWorkspace";
import { ExplorerTree } from "./ExplorerTree";
import { ExplorerSideSections } from "./ExplorerSideSections";
import { NavigatorMenu, type NavigatorMenuItem } from "./NavigatorMenu";
import { NavigatorButton, NavigatorPage, NavigatorPanel, NavigatorToolbar } from "./NavigatorPanel";
import { AgentPanel } from "./AgentPanel";
import { SourceEditor, revealSourceOutline, type SourceModels } from "./SourceEditor";
import { t } from "./i18n";
import { closeDocumentCommand } from "./menus";
import type { StudioLayout } from "./sdk";
import { Icon } from "./ui";
import { compileExcludes, readWorkspaceExcludes } from "./file-excludes";
import {
  forgetLocalEntry,
  listLocalFolder,
  localPickers,
  readLocalRecents,
  readLocalText,
  rememberLocalEntry,
  requestFileAccess,
  type LocalEntry,
  type LocalFolder,
  type RecentLocalEntry,
} from "./welcome-files";
import type { TreeEntry } from "./workspace-model";
import "./welcome-workspace.css";

export type WelcomeEditorState = {
  canSave: boolean;
  canSaveAs: boolean;
  canClose: boolean;
  editorTitle?: string;
};
type LocalDocument = {
  id: string;
  name: string;
  value: string;
  saved: string;
  handle?: FileSystemFileHandle;
};
const noAction = () => {};
const welcomeId = "welcome";
const macKeys = /Mac|iPhone|iPad/.test(navigator.platform);
const activityEmptyHints: Record<Exclude<Activity, "explorer">, string> = {
  search: "全文搜索在打开项目后可用。",
  scm: "源代码管理在打开项目后可用。",
  extensions: "扩展管理在打开项目后可用。",
  library: "部件库在打开项目后可用。",
  instances: "项目实例在打开项目后可用。",
};

export function WelcomeWorkspace({
  ref,
  layout,
  changeLayout,
  openProject,
  activityRequest,
  notify,
  onEditorState,
}: {
  ref: React.Ref<{ run: (id: string) => void }>;
  layout: StudioLayout;
  changeLayout: (patch: Partial<StudioLayout>) => void;
  openProject: () => void;
  activityRequest?: { id: "explorer" | "search" | "scm"; token: number; replace?: boolean };
  notify: (message: string, error?: boolean) => void;
  onEditorState: (state: WelcomeEditorState) => void;
}) {
  const [welcomeOpen, setWelcomeOpen] = useState(true);
  const [active, setActive] = useState<string>(welcomeId);
  const [documents, setDocuments] = useState<LocalDocument[]>([]);
  const [root, setRoot] = useState<LocalFolder>();
  const [excluded, setExcluded] = useState<(relative: string) => boolean>(() => compileExcludes());
  const [directories, setDirectories] = useState<Record<string, LocalEntry[]>>({});
  const [expanded, setExpanded] = useState(() => new Set<string>());
  const [selected, setSelected] = useState("");
  const [cursor, setCursor] = useState<{ line: number; column: number }>();
  const [recents, setRecents] = useState<RecentLocalEntry[]>([]);
  const [focusRecent, setFocusRecent] = useState(0);
  const [workspaceOpen, setWorkspaceOpen] = useState(true);
  const [rootOpen, setRootOpen] = useState(true);
  const recentSection = useRef<HTMLElement>(null);
  const models = useRef<SourceModels>(new Map());
  const sequence = useRef(0);
  const busyRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const current = documents.find((doc) => doc.id === active);
  const dirty = documents.some((doc) => doc.value !== doc.saved);
  const canSave = !!current && (!current.handle || current.value !== current.saved) && !busy;
  const canSaveAs = !!current && !busy;
  const canClose = !!active && !busy;
  const welcomeVisible = welcomeOpen && active === welcomeId;
  const editorTitle = welcomeVisible ? t("欢迎") : current?.name;
  const sidebarActivity = layout.sidebarActivity ?? "explorer";
  const activity = isCoreActivity(sidebarActivity) ? sidebarActivity : "explorer";
  const lastActivityRequest = useRef(0);
  useEffect(() => {
    if (!activityRequest || activityRequest.token === lastActivityRequest.current) return;
    lastActivityRequest.current = activityRequest.token;
    changeLayout({ sidebarOpen: true, sidebarActivity: activityRequest.id });
  }, [activityRequest, changeLayout]);

  useEffect(() => {
    const ownedModels = models.current;
    void readLocalRecents().then(setRecents, noAction);
    return () => ownedModels.forEach(({ model }) => model.dispose());
  }, []);
  useEffect(() => {
    if (!dirty) return;
    const preventUnload = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", preventUnload);
    return () => window.removeEventListener("beforeunload", preventUnload);
  }, [dirty]);
  useEffect(() => {
    onEditorState({ canSave, canSaveAs, canClose, editorTitle });
  }, [canSave, canSaveAs, canClose, editorTitle, onEditorState]);
  useEffect(() => {
    if (!focusRecent) return;
    recentSection.current?.scrollIntoView({ block: "nearest" });
    recentSection.current?.focus({ preventScroll: true });
  }, [focusRecent]);

  async function perform(action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      await action();
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError"))
        notify(t(error instanceof Error ? error.message : String(error)), true);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function remember(handle: LocalEntry) {
    try {
      setRecents(await rememberLocalEntry(handle));
    } catch {
      // An unavailable history store should not prevent opening a file.
    }
  }
  async function openLocalFolder(folder: LocalFolder) {
    const matcher = await readWorkspaceExcludes(folder);
    setExcluded(() => matcher);
    const items = await listLocalFolder(folder, "", matcher);
    setRoot(folder);
    setDirectories({ "": items });
    setExpanded(new Set());
    setSelected("");
    changeLayout({ sidebarOpen: true });
    await remember(folder);
  }
  async function openLocalFile(handle: FileSystemFileHandle) {
    const existing = await Promise.all(
      documents.map((doc) => doc.handle?.isSameEntry(handle).catch(() => false)),
    );
    const index = existing.indexOf(true);
    if (index !== -1) {
      setActive(documents[index].id);
    } else {
      const value = await readLocalText(handle);
      const id = crypto.randomUUID();
      setDocuments((previous) => [
        ...previous,
        { id, name: handle.name, value, saved: value, handle },
      ]);
      setActive(id);
    }
    await remember(handle);
  }
  function openFolder() {
    void perform(async () => {
      if (!localPickers.showDirectoryPicker) throw new Error("此浏览器不支持打开本地文件夹。");
      await openLocalFolder(await localPickers.showDirectoryPicker({ mode: "read" }));
      dismissWelcome();
    });
  }
  function openFile() {
    void perform(async () => {
      if (!localPickers.showOpenFilePicker) throw new Error("此浏览器不支持打开本地文件。");
      const [handle] = await localPickers.showOpenFilePicker();
      if (handle) await openLocalFile(handle);
    });
  }
  function openRecent(handle: LocalEntry) {
    void perform(async () => {
      if (!(await requestFileAccess(handle, "read"))) return;
      if (handle.kind === "directory") {
        await openLocalFolder(handle);
        dismissWelcome();
      } else await openLocalFile(handle);
    });
  }
  function newFile() {
    if (busyRef.current) return;
    const id = crypto.randomUUID();
    const name = t("未命名-{0}", { 0: ++sequence.current }) + ".txt";
    setDocuments((previous) => [...previous, { id, name, value: "", saved: "" }]);
    setActive(id);
  }
  function showWelcome() {
    setWelcomeOpen(true);
    setActive(welcomeId);
  }
  function showRecents() {
    showWelcome();
    setFocusRecent((previous) => previous + 1);
  }
  function dismissWelcome() {
    setWelcomeOpen(false);
    setActive((current) => (current === welcomeId ? (documents.at(-1)?.id ?? "") : current));
  }
  function refreshFolder() {
    if (root) void perform(() => openLocalFolder(root));
  }
  function collapseExplorer() {
    setExpanded(new Set());
  }
  function close(id: string) {
    if (busyRef.current) return;
    const doc = documents.find((item) => item.id === id);
    if (doc && doc.value !== doc.saved && !window.confirm(t("放弃此文件的未保存修改？"))) return;
    if (id === welcomeId) setWelcomeOpen(false);
    else {
      setDocuments((previous) => previous.filter((item) => item.id !== id));
      for (const [path, { model }] of models.current) {
        if (!path.startsWith(id + "/")) continue;
        models.current.delete(path);
        requestAnimationFrame(() => model.dispose());
      }
    }
    if (active === id) {
      setActive(
        documents.filter((item) => item.id !== id).at(-1)?.id ??
          (id !== welcomeId && welcomeOpen ? welcomeId : ""),
      );
    }
  }
  function save(as: boolean) {
    if (!current || (!as && !canSave)) return;
    const { id, value, saved, name } = current;
    void perform(async () => {
      let handle = current.handle;
      if (!handle || as) {
        if (!localPickers.showSaveFilePicker) throw new Error("此浏览器不支持保存本地文件。");
        handle = await localPickers.showSaveFilePicker({ suggestedName: name });
      } else if (!(await requestFileAccess(handle, "readwrite"))) return;
      if (
        current.handle &&
        (await current.handle.isSameEntry(handle)) &&
        (await readLocalText(handle)) !== saved
      ) {
        throw new Error("文件已在外部修改。请另存为其他文件。");
      }
      if (
        (
          await Promise.all(
            documents
              .filter((doc) => doc.id !== id)
              .map((doc) => doc.handle?.isSameEntry(handle).catch(() => false)),
          )
        ).includes(true)
      ) {
        throw new Error("另一个编辑器已打开此文件。请选择其他位置。");
      }
      const writer = await handle.createWritable();
      try {
        await writer.write(value);
        await writer.close();
      } catch (error) {
        await writer.abort().catch(noAction);
        throw error;
      }
      setDocuments((previous) =>
        previous.map((item) =>
          item.id === id ? { ...item, handle, name: handle.name, saved: value } : item,
        ),
      );
      await remember(handle);
      if (root && (!current.handle || as)) {
        // A newly saved file may belong to a directory already expanded in the Explorer.
        const path = await root.resolve(handle).catch(() => null);
        if (path) {
          const parent = path.slice(0, -1).join("/");
          if (Object.hasOwn(directories, parent)) {
            const folder = parent ? handles.get(parent) : root;
            if (folder?.kind === "directory") {
              const items = await listLocalFolder(folder, parent, excluded).catch(() => undefined);
              if (items) setDirectories((previous) => ({ ...previous, [parent]: items }));
            }
          }
        }
      }
    });
  }

  useImperativeHandle(ref, () => ({
    run(id) {
      switch (id) {
        case "file.untitled":
          newFile();
          break;
        case "file.open":
          openFile();
          break;
        case "file.openFolder":
          openFolder();
          break;
        case "file.recent":
          showRecents();
          break;
        case "document.save":
          save(false);
          break;
        case "file.saveAs":
          save(true);
          break;
        case closeDocumentCommand.id:
          close(active);
          break;
        case "help.welcome":
          showWelcome();
          break;
      }
    },
  }));
  useEffect(() => {
    const onSaveKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.key.toLowerCase() !== "s")
        return;
      event.preventDefault();
      if (current) save(event.shiftKey);
    };
    window.addEventListener("keydown", onSaveKey, true);
    return () => window.removeEventListener("keydown", onSaveKey, true);
  });

  const tree: TreeEntry[] = [];
  const handles = new Map<string, LocalEntry>();
  function walk(parent: string, depth: number) {
    for (const handle of Object.hasOwn(directories, parent) ? directories[parent] : []) {
      const path = parent ? parent + "/" + handle.name : handle.name;
      const directory = handle.kind === "directory";
      tree.push({ path, name: handle.name, depth, directory });
      handles.set(path, handle);
      if (directory && expanded.has(path)) walk(path, depth + 1);
    }
  }
  walk("", 0);
  function toggle(path: string) {
    const handle = handles.get(path);
    if (handle?.kind !== "directory") return;
    if (expanded.has(path)) {
      setExpanded((previous) => new Set([...previous].filter((item) => item !== path)));
      return;
    }
    void perform(async () => {
      if (!Object.hasOwn(directories, path)) {
        const children = await listLocalFolder(handle, path, excluded);
        setDirectories((previous) => ({ ...previous, [path]: children }));
      }
      setExpanded((previous) => new Set(previous).add(path));
    });
  }

  const action = (icon: string, label: string, run: () => void, localize = true) => (
    <button className="welcome-link" type="button" disabled={busy} onClick={run}>
      <Icon name={icon} size={17} />
      {localize ? t(label) : label}
    </button>
  );
  const guide = (icon: string, title: string, description: string, link: React.ReactNode) => (
    <details className="welcome-guide">
      <summary>
        <Icon name={icon} size={18} />
        <span>{t(title)}</span>
        <Icon name="chevron" size={14} />
      </summary>
      <div>
        <p>{t(description)}</p>
        {link}
      </div>
    </details>
  );
  const tabs = [
    ...(welcomeOpen
      ? [{ id: welcomeId, label: t("欢迎"), icon: "sparkles", focused: active === welcomeId }]
      : []),
    ...documents.map((doc) => ({
      id: doc.id,
      label: doc.name,
      icon: "file",
      focused: active === doc.id,
      dirty: doc.value !== doc.saved,
    })),
  ].map((tab) => ({ ...tab, action: { label: t("关闭") + ": " + tab.label, icon: "close" } }));
  const closeTabs = (ids: string[]) => ids.forEach((id) => close(id));
  const moreItems: NavigatorMenuItem[] = [
    { label: t("关闭"), run: canClose ? () => close(active) : undefined },
    {
      label: t("关闭其他"),
      run:
        tabs.length > 1
          ? () => closeTabs(tabs.filter((tab) => tab.id !== active).map((tab) => tab.id))
          : undefined,
    },
    {
      label: t("全部关闭"),
      run: tabs.length ? () => closeTabs(tabs.map((tab) => tab.id)) : undefined,
    },
    {
      label: t("关闭已保存"),
      run: documents.some((doc) => doc.value === doc.saved)
        ? () => closeTabs(documents.filter((doc) => doc.value === doc.saved).map((doc) => doc.id))
        : undefined,
    },
  ];
  const showFolders = layout.explorerFolders !== false;
  const showOutline = layout.explorerOutline !== false;
  const showTimeline = layout.explorerTimeline !== false;
  const explorerViewItems: NavigatorMenuItem[] = [
    {
      label: t("文件夹"),
      checkbox: true,
      checked: showFolders,
      run: () => changeLayout({ explorerFolders: !showFolders }),
    },
    {
      label: t("大纲"),
      checkbox: true,
      checked: showOutline,
      run: () => changeLayout({ explorerOutline: !showOutline }),
    },
    {
      label: t("时间线"),
      checkbox: true,
      checked: showTimeline,
      run: () => changeLayout({ explorerTimeline: !showTimeline }),
    },
  ];
  const activityItems = coreItems.map(({ id, label, icon, shortcutKey }) => ({
    id,
    label,
    icon: <Icon name={icon} size={19} />,
    shortcut: shortcutKey
      ? [
          ...(macKeys ? (id === "explorer" ? ["⇧", "⌘"] : ["⌃", "⇧"]) : ["Ctrl", "Shift"]),
          shortcutKey,
        ]
      : undefined,
  }));
  return (
    <WorkbenchLayout
      layout={layout}
      change={changeLayout}
      activity={
        <ActivityBar
          active={activity}
          items={activityItems}
          select={(id) => changeLayout({ sidebarOpen: true, sidebarActivity: id })}
          layout={layout}
          change={changeLayout}
        />
      }
      sidebar={
        <NavigatorPanel
          className="core-sidebar"
          data-activity={activity}
          title={t(coreSidebarTitles[activity])}
          actions={
            activity === "explorer" ? (
              <NavigatorMenu label={t("资源管理器更多操作")} items={explorerViewItems} />
            ) : undefined
          }
        >
          <NavigatorPage hidden={activity !== "explorer"} aria-label={t("资源管理器内容")}>
            <div className="welcome-explorer">
              {showFolders && (
                <>
                  <div className="core-workspace-heading">
                    <button
                      className="core-workspace-toggle"
                      aria-expanded={workspaceOpen}
                      onClick={() => setWorkspaceOpen(!workspaceOpen)}
                    >
                      <span className="core-tree-twistie" aria-hidden="true">
                        <Icon name="chevron" size={12} expanded={workspaceOpen} />
                      </span>
                      <span>{root ? t("工作区") : t("无打开的文件夹")}</span>
                    </button>
                    {workspaceOpen && root && (
                      <NavigatorToolbar
                        className="core-workspace-tools"
                        role="toolbar"
                        aria-label={t("项目文件操作")}
                      >
                        <NavigatorButton
                          label={t("新建文本文件")}
                          icon="new-file"
                          disabled={busy}
                          onClick={newFile}
                        />
                        <NavigatorButton
                          label={t("刷新资源")}
                          icon="refresh"
                          disabled={busy}
                          onClick={refreshFolder}
                        />
                        <NavigatorButton
                          label={t("全部折叠")}
                          icon="collapse"
                          disabled={busy}
                          onClick={collapseExplorer}
                        />
                      </NavigatorToolbar>
                    )}
                  </div>
                  {workspaceOpen &&
                    (root ? (
                      tree.length ? (
                        <ExplorerTree
                          entries={tree}
                          root={{
                            name: root.name,
                            expanded: rootOpen,
                            toggle: () => setRootOpen(!rootOpen),
                          }}
                          expanded={expanded}
                          selected={selected}
                          select={setSelected}
                          toggle={toggle}
                          open={(path) => {
                            const handle = handles.get(path);
                            if (handle?.kind === "file") void perform(() => openLocalFile(handle));
                          }}
                        />
                      ) : (
                        <p className="welcome-explorer-note">{t("此文件夹为空。")}</p>
                      )
                    ) : (
                      <>
                        <p className="welcome-explorer-note">{t("尚未打开文件夹")}</p>
                        <div className="welcome-explorer-actions">
                          <button className="core-primary" disabled={busy} onClick={openFolder}>
                            {t("打开文件夹")}
                          </button>
                          <button className="core-primary" onClick={showRecents}>
                            {t("打开最近的文件")}
                          </button>
                        </div>
                      </>
                    ))}
                </>
              )}
              <ExplorerSideSections
                showOutline={showOutline}
                showTimeline={showTimeline}
                activeDocument={
                  current && active !== welcomeId
                    ? { name: current.name, value: current.value }
                    : undefined
                }
                cursor={cursor}
                onNavigate={(range) => {
                  if (current)
                    revealSourceOutline(current.id + "/" + current.name, models.current, range);
                }}
                initialHeights={{ outline: layout.outlineHeight, timeline: layout.timelineHeight }}
                onCommit={(heights) =>
                  changeLayout({ outlineHeight: heights.outline, timelineHeight: heights.timeline })
                }
              />
            </div>
          </NavigatorPage>
          {(Object.keys(activityEmptyHints) as Exclude<Activity, "explorer">[]).map((id) => (
            <NavigatorPage key={id} hidden={activity !== id} aria-label={t(coreSidebarTitles[id])}>
              <div className="core-scm-empty">
                <Icon name={coreItems.find((item) => item.id === id)?.icon ?? "files"} size={26} />
                <p>{t(activityEmptyHints[id])}</p>
                <button className="core-primary" onClick={openProject}>
                  {t("打开已启动的项目")}
                </button>
              </div>
            </NavigatorPage>
          ))}
        </NavigatorPanel>
      }
      inspector={
        <AgentPanel
          workspace="empty"
          visible={layout.inspectorOpen}
          maximized={Boolean(layout.inspectorMaximized)}
          openProject={openProject}
          onToggleMaximize={() =>
            changeLayout({ inspectorMaximized: !layout.inspectorMaximized, inspectorOpen: true })
          }
          onClose={() => changeLayout({ inspectorOpen: false, inspectorMaximized: false })}
        />
      }
      bottom={
        <WorkbenchBottomPanel
          layout={layout}
          changeLayout={changeLayout}
          taskPanel={<p className="core-hint">{t("打开项目后可查看任务与日志。")}</p>}
          dirty={false}
          notify={notify}
        />
      }
    >
      <div className="editor-area editor-area--chrome-tabs-only">
        <div className="core-document-tabs">
          <div className="core-document-tab-list">
            <WorkspaceTabStrip
              className="welcome-tabs"
              label={t("编辑器标签页")}
              tabs={tabs}
              onSelect={setActive}
              onAction={close}
            />
          </div>
          <div className="core-document-actions" role="group" aria-label={t("文档操作")}>
            <NavigatorMenu
              label={t("更多操作")}
              items={moreItems}
              triggerClassName="file-editor-more-action"
            />
          </div>
        </div>
        {active === welcomeId ? (
          <div className="welcome-scroll" role="tabpanel" aria-label={t("欢迎")}>
            <div className="welcome-page">
              <header>
                <h1>Tex Studio</h1>
                <p>{t("从文件、文件夹或已有工程开始。")}</p>
              </header>
              <div className="welcome-columns">
                <div>
                  <section aria-labelledby="welcome-start-title">
                    <h2 id="welcome-start-title">{t("启动")}</h2>
                    {action("new-file", "新建文本文件", newFile)}
                    {action("file", "打开文件…", openFile)}
                    {action("folder", "打开文件夹…", openFolder)}
                    {action("grid", "打开已启动的项目", openProject)}
                  </section>
                  <section
                    className="welcome-recents"
                    aria-labelledby="welcome-recent-title"
                    ref={recentSection}
                    tabIndex={-1}
                  >
                    <h2 id="welcome-recent-title">{t("最近")}</h2>
                    {recents.length ? (
                      recents.map(({ id, handle }) => (
                        <div className="welcome-recent-row" key={id}>
                          {action(
                            handle.kind === "directory" ? "folder" : "file",
                            handle.name,
                            () => openRecent(handle),
                            false,
                          )}
                          <button
                            className="icon-button welcome-forget"
                            type="button"
                            disabled={busy}
                            aria-label={t("从最近列表移除 {0}", { 0: handle.name })}
                            title={t("从最近列表移除 {0}", { 0: handle.name })}
                            onClick={() =>
                              void perform(async () => setRecents(await forgetLocalEntry(id)))
                            }
                          >
                            <Icon name="close" size={14} />
                          </button>
                        </div>
                      ))
                    ) : (
                      <p>{t("最近打开的本地文件和文件夹会显示在这里。")}</p>
                    )}
                  </section>
                </div>
                <section aria-labelledby="welcome-guides-title">
                  <h2 id="welcome-guides-title">{t("演练")}</h2>
                  {guide(
                    "sparkles",
                    "开始使用 Tex Studio",
                    "打开已启动的工程，即可使用工程检查、模型和专业图纸。",
                    action("grid", "打开已启动的项目", openProject),
                  )}
                  {guide(
                    "folder",
                    "浏览本地文件夹",
                    "选择一个文件夹，在资源管理器中浏览，并打开其中的文本文件。",
                    action("folder", "打开文件夹…", openFolder),
                  )}
                  {guide(
                    "edit",
                    "编辑和保存文本",
                    "新建文本或打开现有文件，在文件菜单中选择保存。",
                    action("new-file", "新建文本文件", newFile),
                  )}
                </section>
              </div>
            </div>
          </div>
        ) : current ? (
          <div className="welcome-document" role="tabpanel" aria-label={current.name}>
            <SourceEditor
              path={current.id + "/" + current.name}
              value={current.value}
              models={models.current}
              visible
              onChange={(value) =>
                setDocuments((previous) =>
                  previous.map((doc) => (doc.id === current.id ? { ...doc, value } : doc)),
                )
              }
              onFocusHandled={noAction}
              onStatus={(status) => setCursor({ line: status.line, column: status.column })}
            />
          </div>
        ) : (
          <div className="welcome-no-editor">{action("sparkles", "显示欢迎页", showWelcome)}</div>
        )}
      </div>
    </WorkbenchLayout>
  );
}
