import React, { useEffect, useImperativeHandle, useRef, useState } from "react";
import { WorkbenchLayout } from "./atomstudio/Layout";
import { WorkspaceTabStrip } from "./atomstudio/WorkspaceTabs";
import { ExplorerTree } from "./ExplorerTree";
import { SourceEditor, type SourceModels } from "./SourceEditor";
import { t } from "./i18n";
import { closeDocumentCommand } from "./menus";
import type { StudioLayout } from "./sdk";
import { Icon } from "./ui";
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

export type WelcomeEditorState = { canSave: boolean; canSaveAs: boolean; canClose: boolean };
type LocalDocument = {
  id: string;
  name: string;
  value: string;
  saved: string;
  handle?: FileSystemFileHandle;
};
const noAction = () => {};
const welcomeId = "welcome";

export function WelcomeWorkspace({
  ref,
  layout,
  changeLayout,
  openProject,
  notify,
  onEditorState,
}: {
  ref: React.Ref<{ run: (id: string) => void }>;
  layout: StudioLayout;
  changeLayout: (patch: Partial<StudioLayout>) => void;
  openProject: () => void;
  notify: (message: string, error?: boolean) => void;
  onEditorState: (state: WelcomeEditorState) => void;
}) {
  const [welcomeOpen, setWelcomeOpen] = useState(true);
  const [active, setActive] = useState<string>(welcomeId);
  const [documents, setDocuments] = useState<LocalDocument[]>([]);
  const [root, setRoot] = useState<LocalFolder>();
  const [directories, setDirectories] = useState<Record<string, LocalEntry[]>>({});
  const [expanded, setExpanded] = useState(() => new Set<string>());
  const [selected, setSelected] = useState("");
  const [recents, setRecents] = useState<RecentLocalEntry[]>([]);
  const [focusRecent, setFocusRecent] = useState(0);
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
    onEditorState({ canSave, canSaveAs, canClose });
  }, [canSave, canSaveAs, canClose, onEditorState]);
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
    const items = await listLocalFolder(folder);
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
      if (handle.kind === "directory") await openLocalFolder(handle);
      else await openLocalFile(handle);
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
              const items = await listLocalFolder(folder).catch(() => undefined);
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
        const children = await listLocalFolder(handle);
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
  return (
    <WorkbenchLayout
      layout={layout}
      change={changeLayout}
      activity={
        <div className="activity-bar" role="tablist" aria-label={t("资源入口")}>
          <button
            role="tab"
            aria-label={t("文件资源管理器")}
            aria-controls="studio-resource-panel"
            aria-selected={layout.sidebarOpen}
            onClick={() => changeLayout({ sidebarOpen: !layout.sidebarOpen })}
          >
            <Icon name="files" size={20} />
          </button>
        </div>
      }
      sidebar={
        <div className="welcome-explorer">
          <div className="core-heading">
            <strong>{t("文件资源管理器")}</strong>
          </div>
          <div className="welcome-explorer-caption">
            <Icon name="chevron-down" size={14} />
            <strong>{root?.name ?? t("无打开的文件夹")}</strong>
          </div>
          {root ? (
            tree.length ? (
              <ExplorerTree
                entries={tree}
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
            <p className="welcome-explorer-note">{t("尚未打开文件夹")}</p>
          )}
          <div className="welcome-explorer-actions">
            <button className="core-primary" disabled={busy} onClick={openFolder}>
              {t("打开文件夹")}
            </button>
            <button className="core-primary" onClick={showRecents}>
              {t("打开最近的文件")}
            </button>
          </div>
        </div>
      }
      inspector={null}
      bottom={null}
    >
      <div className="editor-area editor-area--chrome-tabs-only">
        <WorkspaceTabStrip
          className="welcome-tabs"
          label={t("编辑器标签页")}
          tabs={tabs}
          onSelect={setActive}
          onAction={close}
        />
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
            />
          </div>
        ) : (
          <div className="welcome-no-editor">{action("sparkles", "显示欢迎页", showWelcome)}</div>
        )}
      </div>
    </WorkbenchLayout>
  );
}
