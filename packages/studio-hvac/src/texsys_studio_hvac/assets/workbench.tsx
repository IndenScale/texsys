import { t, getLocale, useLocale } from "../../../../../src/texsys/assets/studio/i18n";
import {
  WorkbenchSurface,
  useCoreWorkspace,
} from "../../../../../src/texsys/assets/studio/CoreWorkspace";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { WorkbenchPlugin, WorkbenchProps } from "../../../../../src/texsys/assets/studio/sdk";
import { labels } from "../../../../../src/texsys/assets/studio/sdk";
import { Empty, Icon } from "../../../../../src/texsys/assets/studio/ui";
import { EditorActionButton } from "../../../../../src/texsys/assets/studio/EditorActionButton";
import {
  DiagramSvg,
  EngineeringSymbol,
  parameterTitles,
} from "../../../../hvac-graphics/src/texsys_hvac_graphics/assets/graphics";
import {
  RunPanel,
  backendName,
  scenarioNames,
} from "../../../../hvac-graphics/src/texsys_hvac_graphics/assets/simulation";
import { assertionLocation } from "../../../../hvac-graphics/src/texsys_hvac_graphics/assets/analysis";
import { resolveGraphic } from "../../../../hvac-graphics/src/texsys_hvac_graphics/assets/symbols";
import { Inspector } from "./inspector";
import {
  bindingValue,
  download,
  instanceFromRef,
  issuesFor,
  names,
  sourceObjectLine,
  type Data,
  type Issue,
  type Selection,
} from "./model";
import { EditorArea } from "../../../../../src/texsys/assets/studio/atomstudio/EditorArea";
import { WorkspaceTabStrip } from "../../../../../src/texsys/assets/studio/atomstudio/WorkspaceTabs";
import {
  visitDocument,
  stepDocument,
  closeDocumentFromList,
  type DocumentHistory,
} from "../../../../../src/texsys/assets/studio/navigation";
import { FileDocument } from "../../../../../src/texsys/assets/studio/FileDocument";
import { WorkbenchBottomPanel } from "../../../../../src/texsys/assets/studio/BottomPanel";
import { useDrawingViewport } from "./drawingViewport";
import "./workbench.css";

type View =
  | "file"
  | "overview"
  | "design"
  | "equipment"
  | "components"
  | "simulation"
  | "checks"
  | "source"
  | "outputs"
  | "capabilities";
const views: { id: View; label: string; icon: string; group: string; description: string }[] = [
  {
    id: "file",
    label: "文件预览",
    icon: "file",
    group: "项目",
    description: "查看项目文件原始文本。",
  },
  {
    id: "overview",
    label: "项目概览",
    icon: "folder",
    group: "项目",
    description: "从当前设计继续工作，查看模型、运行与成果状态。",
  },
  {
    id: "design",
    label: "系统图与原理图",
    icon: "system",
    group: "设计",
    description: "查看系统连接，选择设备编辑实例参数。",
  },
  {
    id: "equipment",
    label: "设备与参数",
    icon: "table",
    group: "设计",
    description: "同一系统的设备表，与图面和实例属性关联。",
  },
  {
    id: "components",
    label: "器件类型库",
    icon: "cube",
    group: "设计",
    description: "项目内的可复用类型定义；默认参数与实例覆盖分别查看。",
  },
  {
    id: "source",
    label: "模型源数据",
    icon: "code",
    group: "设计",
    description: "查看参数编辑对应的 JSON 源数据，支持对象定位。",
  },
  {
    id: "simulation",
    label: "工况与结果",
    icon: "chart",
    group: "分析",
    description: "选择工况运行，查看响应、约束与异常时刻。",
  },
  {
    id: "checks",
    label: "检查与问题",
    icon: "check",
    group: "分析",
    description: "模型诊断与工况约束集中查看，按对象定位。",
  },
  {
    id: "outputs",
    label: "成果与导出",
    icon: "download",
    group: "成果",
    description: "按输入版本查看图纸、模型、设备表与计算结果。",
  },
  {
    id: "capabilities",
    label: "能力与范围",
    icon: "info",
    group: "",
    description: "当前工作台的能力与适用范围。",
  },
];
const info = (view: View) => views.find((item) => item.id === view)!;

function IssueRows({ issues, locate }: { issues: Issue[]; locate: (issue: Issue) => void }) {
  return (
    <div className="issue-list">
      {issues.map((issue) => (
        <div className={`issue-row ${issue.current ? "" : "historical"}`} key={issue.id}>
          <span className={`tag ${issue.status}`}>
            {({ error: t("错误"), warning: t("警告") } as Record<string, string>)[issue.status] ??
              t(labels[issue.status]) ??
              t("待处理")}
          </span>
          <div className="issue-text">
            <strong>{issue.title}</strong>
            <p>{issue.detail}</p>
            <small>
              {issue.origin}
              {!issue.current && t(" · 历史输入")}
            </small>
          </div>
          {issue.runPath || issue.object ? (
            <EditorActionButton
              icon="locate"
              label={t("定位问题 {0} {1}", { "0": issue.title, "1": issue.origin })}
              onClick={() => locate(issue)}
            />
          ) : (
            <span className="muted issue-unlocated">{t("无对象定位")}</span>
          )}
        </div>
      ))}
    </div>
  );
}

function Workspace({
  state,
  data,
  refresh,
  api,
  notify,
  showTasks,
  taskResult,
  layout,
  changeLayout,
  taskPanel,
  navigationRequest,
  onNavigationState,
}: WorkbenchProps & { data: Data }) {
  const locale = useLocale();
  const core = useCoreWorkspace();
  const activateDomain = core?.activateDomain;
  const source = data.source!;
  const instances = source.components[source.root]?.instances ?? {};
  const entries = Object.entries(instances);
  const [view, setView] = useState<View>("design");
  const [filePath, setFilePath] = useState("");
  const [history, setHistory] = useState<DocumentHistory<View>>({ entries: ["design"], index: 0 });
  const [documents, setDocuments] = useState<View[]>(["design"]);
  const [selection, setSelection] = useState<Selection | undefined>(() =>
    entries[0] ? { kind: "instance", id: entries[0][0] } : undefined,
  );
  const [search, setSearch] = useState("");
  const [drawingView, setDrawingView] = useState("schematic");
  const [overlay, setOverlay] = useState(false);
  const [debug, setDebug] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [locateToken, setLocateToken] = useState(0);
  const [playback, setPlayback] = useState({
    path: "",
    frame: 0,
    playing: false,
    variable: "",
    token: 0,
  });
  const [scenario, setScenario] = useState("all");
  const [backend, setBackend] = useState("scipy");
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [hasDraft, setHasDraft] = useState(false);
  const [undo, setUndo] = useState<{ operation: Record<string, unknown>; revision: string }>();
  const [issueScope, setIssueScope] = useState("current");
  const [issueSearch, setIssueSearch] = useState("");
  const [librarySearch, setLibrarySearch] = useState("");
  const [sourceSearch, setSourceSearch] = useState("");
  const [lastFocus, setLastFocus] = useState("");
  const viewport = useRef<HTMLDivElement>(null);
  const sourceViewport = useRef<HTMLDivElement>(null);
  const contentViewport = useRef<HTMLDivElement>(null);
  const scrollPositions = useRef<Partial<Record<View, number>>>({});
  const requestPending = useRef(false);

  const selected = selection?.kind === "instance" ? selection.id : "";
  const drawing = data.diagrams[drawingView];
  const diagram = drawing?.document;
  const node = diagram?.nodes.find((item) => item.semantic_ref === `${source.root}.${selected}`);
  const foundIndex = data.runs.findIndex((item) => item.path === playback.path);
  const runs = data.runs,
    runIndex = Math.max(0, foundIndex),
    run = runs[runIndex];
  const safeFrame = run ? Math.max(0, Math.min(playback.frame, run.result.time_s.length - 1)) : 0;
  const busy = state.jobs.some((item) => item.state === "running");
  const issues = useMemo(() => issuesFor(data), [data, locale]);
  const currentIssues = issues.filter((item) => item.current);
  const visibleIssues = issues.filter(
    (item) =>
      (issueScope === "all" || (issueScope === "current" ? item.current : !item.current)) &&
      `${item.title} ${item.detail} ${item.origin} ${item.object ?? ""}`
        .toLowerCase()
        .includes(issueSearch.toLowerCase()),
  );
  const types = Object.entries(source.components).filter(
    ([id, definition]) => id !== source.root && !definition.instances,
  );
  const filtered = entries.filter(([name, item]) =>
    `${name} ${item.component} ${t(names[item.component]) ?? ""}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const sourceLines = JSON.stringify(source, null, 2).split("\n");
  const objectLine = sourceObjectLine(source, selection);
  const openView = useCallback(
    (next: View) => {
      activateDomain?.();
      setDocuments((previous) => (previous.includes(next) ? previous : [...previous, next]));
      setView(next);
      setHistory((previous) => visitDocument(previous, next));
      setPlayback((previous) => ({ ...previous, playing: false }));
    },
    [activateDomain],
  );
  const setInspectorOpen = (inspectorOpen: boolean) => changeLayout({ inspectorOpen });
  const choose = (next: Selection, reveal = true) => {
    if (hasDraft && (next.id !== selection?.id || next.kind !== selection?.kind)) {
      setInspectorOpen(true);
      notify(t("请先保存或放弃右侧的参数草稿，再切换对象。"), true);
      return false;
    }
    setSelection(next);
    if (next.id !== selection?.id || next.kind !== selection?.kind)
      setPlayback((previous) => ({ ...previous, variable: "", token: previous.token + 1 }));
    if (reveal) setInspectorOpen(true);
    return true;
  };
  const chooseRun = (index: number) => {
    if (!runs[index]) return;
    setPlayback((previous) => ({
      path: runs[index].path,
      frame: 0,
      playing: false,
      variable: "",
      token: previous.token + 1,
    }));
    setLastFocus("");
  };
  const locateAssertion = (path: string, assertionIndex: number) => {
    const target = runs.find((item) => item.path === path),
      assertion = target?.result.assertions[assertionIndex];
    if (!target || !assertion) return;
    const variable = assertion.variable ? target.result.variables[assertion.variable] : undefined;
    const object = variable ? instanceFromRef(source, variable.semantic_ref) : undefined;
    if (object && !choose({ kind: "instance", id: object }, false)) return;
    const location = assertionLocation(target.result, assertion);
    setPlayback((previous) => ({
      path,
      frame: location.index,
      playing: false,
      variable: assertion.variable ?? "",
      token: previous.token + 1,
    }));
    setLastFocus(
      `${object ?? t("约束")} · ${location.reason} ${target.result.time_s[location.index]} s`,
    );
    openView("simulation");
  };
  const locateIssue = (issue: Issue) => {
    if (issue.runPath && issue.assertion !== undefined)
      locateAssertion(issue.runPath, issue.assertion);
    else if (issue.object && choose({ kind: "instance", id: issue.object }, false)) {
      openView("design");
      setLocateToken((previous) => previous + 1);
    }
  };
  useEffect(() => {
    if (!playback.playing || !run) return;
    const timer = setInterval(
      () =>
        setPlayback((previous) =>
          previous.frame >= run.result.time_s.length - 1
            ? { ...previous, playing: false }
            : { ...previous, frame: previous.frame + 1 },
        ),
      100,
    );
    return () => clearInterval(timer);
  }, [playback.playing, run?.path]);
  useEffect(() => {
    setZoom(1);
  }, [drawingView]);
  useEffect(() => {
    if (contentViewport.current)
      contentViewport.current.scrollTop = scrollPositions.current[view] ?? 0;
  }, [view]);
  const drawingCamera = useDrawingViewport(
    viewport,
    view === "design" && !!diagram && !core?.documentActive,
    `${drawingView}:${diagram?.pages[0]?.width}:${diagram?.pages[0]?.height}`,
    diagram?.pages[0]?.width ?? 1,
    diagram?.pages[0]?.height ?? 1,
    () => setZoom(1),
  );
  useEffect(() => {
    if (view === "source" && objectLine >= 0)
      sourceViewport.current
        ?.querySelector(`[data-line="${objectLine}"]`)
        ?.scrollIntoView({ block: "center" });
  }, [view, selection?.id, objectLine]);
  useEffect(() => {
    if (view !== "design" || !locateToken) return;
    drawingCamera.locate(
      Array.from(viewport.current?.querySelectorAll("[data-semantic-ref]") ?? []).find(
        (item) => item.getAttribute("data-semantic-ref") === `${source.root}.${selected}`,
      ),
    );
  }, [view, locateToken]);
  useEffect(() => {
    if (!taskResult || taskResult.job.plugin !== "texsys:hvac") return;
    const requested = taskResult.job;
    if (requested.action === "simulate") {
      const index = runs.findIndex((item) => item.path.includes(`/jobs/${requested.id}/`));
      if (index >= 0) chooseRun(index);
      else notify(t("此运行结果已由较新结果替代，可在任务日志中查看原运行记录。"));
      openView("simulation");
    } else openView(requested.action === "check" ? "checks" : "outputs");
  }, [taskResult?.token]);
  useEffect(() => {
    onNavigationState?.({
      canBack: history.index > 0,
      canForward: history.index < history.entries.length - 1,
      canClose: documents.length > 0,
    });
  }, [history, documents.length, onNavigationState]);
  useEffect(() => {
    core?.setDomainTabsOpen?.(documents.length > 0);
  }, [core?.setDomainTabsOpen, documents.length]);
  useEffect(() => {
    if (core && !core.documentActive && documents.length === 0) {
      openView("overview");
    }
  }, [core?.documentActive, documents.length, openView]);
  useEffect(() => {
    if (!navigationRequest) return;
    const command = navigationRequest.command;
    if (command === "file.open" && navigationRequest.path) {
      setFilePath(navigationRequest.path);
      openView("file");
    } else if (command === "document.close") {
      if (view && documents.includes(view)) closeDocument(view);
    } else if (command === "navigation.back" || command === "navigation.forward") {
      if (core?.documentActive) return;
      const next = stepDocument(history, command === "navigation.back" ? -1 : 1);
      if (next === history) return;
      const target = next.entries[next.index];
      setHistory(next);
      setDocuments((previous) => (previous.includes(target) ? previous : [...previous, target]));
      setView(target);
      setPlayback((previous) => ({ ...previous, playing: false }));
    } else if (views.some((item) => item.id === command)) openView(command as View);
  }, [navigationRequest?.token, openView]);
  const colors = useMemo(() => {
    const temperatures: Record<string, number> = {},
      flows: Record<string, number> = {};
    if (overlay && run?.current && drawing?.current)
      for (const [key, item] of Object.entries(run.result.variables)) {
        if (item.role === "fluid-temperature" && !key.includes("#cooling"))
          temperatures[item.semantic_ref] = item.values[safeFrame];
        if (item.role === "connection-flow") flows[item.semantic_ref] = item.values[safeFrame];
      }
    return { temperatures, flows };
  }, [overlay, run, safeFrame, drawing?.current]);
  const save = async (operation: Record<string, unknown>, revision: string) => {
    setSaving(true);
    try {
      const response = await api<{ undo: Record<string, unknown>; revision: string }>("/api/edit", {
        plugin: "texsys:hvac",
        revision,
        operation,
      });
      setUndo({ operation: response.undo, revision: response.revision });
      await refresh();
      notify(t("参数已保存。请更新受影响的图纸、检查与仿真结果。"));
    } finally {
      setSaving(false);
    }
  };
  const submit = async (action: string) => {
    if (busy || requestPending.current || hasDraft) return;
    requestPending.current = true;
    setSubmitting(true);
    try {
      await api("/api/jobs", {
        plugin: "texsys:hvac",
        action,
        revision: state.project.revision,
        options: action === "simulate" ? { scenario, backend } : {},
      });
      await refresh();
      notify(t("{0}已启动，可继续查看设计。", { "0": t(labels[action]) }));
    } catch (error) {
      notify(error instanceof Error ? error.message : String(error), true);
    } finally {
      requestPending.current = false;
      setSubmitting(false);
    }
  };
  const exportArtifact = async (path: string, name: string) => {
    const token =
      document.querySelector<HTMLMetaElement>('meta[name="tex-studio-token"]')?.content ?? "";
    try {
      const response = await fetch(`/api/artifact?path=${encodeURIComponent(path)}`, {
        headers: { "X-Tex-Studio-Token": token },
      });
      if (!response.ok) throw new Error(t("无法读取导出文件"));
      download(await response.blob(), name);
      notify(t("已导出 {0}", { "0": name }));
    } catch (error) {
      notify(String(error), true);
    }
  };
  const exportEquipment = () => {
    const rows = [
      [t("实例"), t("器件类型"), t("配置")],
      ...entries.map(([name, item]) => [
        name,
        item.component,
        Object.keys(item.parameters ?? {}).length ? t("实例覆盖") : t("继承配置"),
      ]),
    ];
    download(
      "\ufeff" +
        rows
          .map((row) => row.map((cell) => `"${cell.replaceAll('"', '""')}"`).join(","))
          .join("\n"),
      t("{0}-设备表.csv", { "0": source.root }),
      "text/csv;charset=utf-8",
    );
  };
  const runPanel = run && (
    <RunPanel
      ActionButton={EditorActionButton}
      runs={runs.map((item) => item.result)}
      runIndex={runIndex}
      setRunIndex={chooseRun}
      index={safeFrame}
      setIndex={(frame) => setPlayback((previous) => ({ ...previous, frame, playing: false }))}
      playing={playback.playing}
      setPlaying={(playing) => setPlayback((previous) => ({ ...previous, playing }))}
      selected={selected ? `${source.root}.${selected}` : undefined}
      focus={{ variable: playback.variable, token: playback.token }}
      onLocateAssertion={(index) => locateAssertion(run.path, index)}
      onVariableChange={(variable) =>
        setPlayback((previous) => ({ ...previous, variable, token: previous.token + 1 }))
      }
    />
  );
  const canRun = !busy && !submitting && !saving && !hasDraft;
  const closeDocument = (target: View) => {
    const { remaining, next } = closeDocumentFromList(documents, target);
    setDocuments(remaining);
    if (view === target) {
      if (next) openView(next);
      else core?.activateCore?.();
    }
    setPlayback((previous) => ({ ...previous, playing: false }));
  };
  const tabs = documents.length ? (
    <WorkspaceTabStrip
      label={t("打开的文档")}
      tabs={documents.map((id) => ({
        id,
        label:
          id === "file"
            ? (filePath.split("/").pop() ?? t("文件预览"))
            : id === "design"
              ? source.root
              : t(info(id).label),
        icon: info(id).icon,
        focused: view === id && !core?.documentActive,
        dirty: hasDraft && ["design", "equipment", "source"].includes(id),
        dirtyLabel: t("有未保存参数"),
        action: { label: t("关闭文档 {0}", { "0": t(info(id).label) }), icon: "close" },
      }))}
      onSelect={(id) => openView(id as View)}
      onAction={(id) => closeDocument(id as View)}
    />
  ) : null;
  const breadcrumb = (
    <>
      <select
        className="editor-view-select"
        aria-label={t("当前编辑视图")}
        value={["design", "equipment", "source"].includes(view) ? view : "other"}
        onChange={(event) => openView(event.target.value as View)}
      >
        <option value="design">
          {source.root} {t("· 图纸")}
        </option>
        <option value="equipment">
          {source.root} {t("· 设备表")}
        </option>
        <option value="source">
          {source.root} {t("· 源数据")}
        </option>
        {!["design", "equipment", "source"].includes(view) && (
          <option value="other">{t(info(view).label)}</option>
        )}
      </select>
      {view === "design" && (
        <>
          <span className="context-separator">/</span>
          <select
            className="editor-view-select drawing-view-select"
            aria-label={t("图纸类型")}
            value={drawingView}
            onChange={(event) => setDrawingView(event.target.value)}
          >
            <option value="schematic">{t("原理图")}</option>
            <option value="system">{t("系统图")}</option>
          </select>
          <span className="context-separator">/</span>
          <select
            className="editor-view-select drawing-mode-select"
            aria-label={t("画布显示模式")}
            value={overlay ? "results" : "design"}
            onChange={(event) => {
              setOverlay(event.target.value === "results");
              setPlayback((previous) => ({ ...previous, playing: false }));
            }}
          >
            <option value="design">{t("设计参数")}</option>
            <option value="results" disabled={!runs.length}>
              {t("结果叠加")}
            </option>
          </select>
          <output className="drawing-zoom">{Math.round(zoom * 100)}%</output>
        </>
      )}
      {hasDraft && (
        <span className="draft-status">
          <span className="draft-dot" aria-hidden="true" />
          {t("参数未保存")}
        </span>
      )}
    </>
  );
  const tabActions = (
    <>
      {view === "design" && (
        <div className="drawing-tab-actions">
          <EditorActionButton
            label={t("接口标记")}
            icon="system"
            aria-pressed={debug}
            onClick={() => setDebug(!debug)}
          />
          <EditorActionButton
            label={t("缩小图纸")}
            icon="minus"
            onClick={() => setZoom(Math.max(0.4, zoom - 0.2))}
          />
          <EditorActionButton
            label={t("放大图纸")}
            icon="plus"
            onClick={() => setZoom(Math.min(4, zoom + 0.2))}
          />
          <EditorActionButton label={t("适合图面")} icon="fit" onClick={drawingCamera.fit} />
        </div>
      )}
      {hasDraft && (
        <EditorActionButton
          label={t("查看未保存参数")}
          icon="inspector"
          onClick={() => setInspectorOpen(true)}
        />
      )}
      {undo && (
        <EditorActionButton
          label={t("撤销上次参数修改")}
          icon="undo"
          disabled={saving || hasDraft}
          onClick={() =>
            void save(undo.operation, undo.revision).catch((error) => notify(String(error), true))
          }
        />
      )}
      {["design", "equipment"].includes(view) && (
        <>
          <EditorActionButton
            label={t("检查模型")}
            icon="check"
            disabled={!canRun}
            onClick={() => void submit("check")}
          />
          <EditorActionButton
            label={t("生成图纸")}
            icon="layers"
            disabled={!canRun}
            onClick={() => void submit("build")}
          />
        </>
      )}
      {view === "checks" && (
        <EditorActionButton
          label={t("运行模型检查")}
          icon="check"
          disabled={!canRun}
          onClick={() => void submit("check")}
        />
      )}
      {view === "outputs" && (
        <EditorActionButton
          label={t("更新图纸")}
          icon="layers"
          disabled={!canRun}
          onClick={() => void submit("build")}
        />
      )}
      {view === "source" && (
        <EditorActionButton
          label={t("导出模型")}
          icon="download"
          onClick={() => download(JSON.stringify(source, null, 2) + "\n", "system.sdl.json")}
        />
      )}
    </>
  );
  const bottom = (
    <WorkbenchBottomPanel
      layout={layout}
      changeLayout={changeLayout}
      problemsCount={currentIssues.length}
      problemsContent={
        currentIssues.length ? (
          <IssueRows issues={currentIssues} locate={locateIssue} />
        ) : (
          <p className="resource-hint">{t("当前没有待处理问题。检查范围见“检查与问题”。")}</p>
        )
      }
      openChecks={() => openView("checks")}
      taskPanel={taskPanel}
      key={state.project.path}
      dirty={state.project.branch?.kind === "branch"}
      notify={notify}
    />
  );
  return (
    <div className="hvac-workbench">
      <WorkbenchSurface
        layout={layout}
        change={changeLayout}
        tabs={tabs}
        tabActions={
          hasDraft || undo || ["design", "equipment", "checks", "outputs", "source"].includes(view)
            ? tabActions
            : null
        }
        bottom={bottom}
        inspector={
          <Inspector
            selection={selection}
            source={source}
            sourcePath={data.source_path}
            revision={state.project.revision}
            save={save}
            busy={saving}
            onDirtyChange={setHasDraft}
            onSelectInstance={(id) => {
              if (choose({ kind: "instance", id })) openView("equipment");
            }}
            onLocate={(next) => {
              openView(next);
              setLocateToken((previous) => previous + 1);
            }}
          />
        }
      >
        <EditorArea breadcrumb={breadcrumb} chrome="breadcrumb-only" layout="full-bleed">
          <div
            id="hvac-document-panel"
            role="tabpanel"
            aria-label={t(info(view).label)}
            className={`workbench-content view-${view}`}
            ref={contentViewport}
            onScroll={(event) => {
              if (event.target === event.currentTarget)
                scrollPositions.current[view] = event.currentTarget.scrollTop;
            }}
          >
            {view === "file" && <FileDocument path={filePath} api={api} />}
            {view === "overview" && (
              <div className="data-view project-overview">
                <div className="overview-heading">
                  <Icon name="system" size={30} />
                  <div>
                    <h2>{state.project.name}</h2>
                    <p>{t("暖通系统设计 · 本地项目")}</p>
                  </div>
                  <span className="tag warning">{t("概念设计")}</span>
                </div>
                <p className="project-location">{state.project.path}</p>
                <section className="continue-design">
                  <div>
                    <h3>
                      {t("继续设计")}
                      {source.root}
                    </h3>
                    <p>
                      {entries.length}
                      {t("个设备 ·")} {source.components[source.root]?.connections?.length ?? 0}
                      {t("条连接 ·")} {types.length}
                      {t("类器件")}
                    </p>
                  </div>
                  <EditorActionButton
                    label={t("打开系统图")}
                    icon="arrow"
                    emphasis
                    onClick={() => openView("design")}
                  />
                </section>
                <h3>{t("项目工作")}</h3>
                <div className="project-work-list">
                  {[
                    ["equipment", t("核对设备与参数"), t("查看实例覆盖、继承与参数依据")],
                    ["components", t(info("components").label), t(info("components").description)],
                    [
                      "simulation",
                      t("运行与分析工况"),
                      t("{0} 个工况配置，{1} 份当前结果", {
                        "0": data.scenarios.length,
                        "1": runs.filter((item) => item.current).length,
                      }),
                    ],
                    [
                      "checks",
                      t("处理检查问题"),
                      currentIssues.length
                        ? t("{0} 个当前问题待处理", { "0": currentIssues.length })
                        : t("查看检查与工况约束；未运行不代表通过"),
                    ],
                    [
                      "outputs",
                      t("整理成果"),
                      t("{0} 份当前图纸文件", {
                        "0": data.exports.filter((item) => item.current).length,
                      }),
                    ],
                  ].map(([id, title, detail]) => (
                    <button key={id} onClick={() => openView(id as View)}>
                      <Icon name={info(id as View).icon} size={21} />
                      <span>
                        <strong>{title}</strong>
                        <small>{detail}</small>
                      </span>
                      <Icon name="arrow" size={16} />
                    </button>
                  ))}
                </div>
                <h3>{t("最近运行")}</h3>
                <div className="recent-jobs">
                  {state.jobs
                    .filter((item) => item.plugin === "texsys:hvac")
                    .slice(0, 4)
                    .map((item) => (
                      <button key={item.id} onClick={() => showTasks(item.id)}>
                        <Icon name="terminal" size={17} />
                        <span>{t(labels[item.action])}</span>
                        <time>{new Date(item.created_at).toLocaleString(getLocale())}</time>
                        <span className={`tag ${item.state}`}>{t(labels[item.state])}</span>
                      </button>
                    ))}
                  {!state.jobs.length && <p className="muted">{t("尚未运行检查、出图或仿真。")}</p>}
                </div>
              </div>
            )}
            {view === "design" && (
              <>
                {drawing && !drawing.current && (
                  <div className="stale-banner">
                    <Icon name="clock" size={16} />
                    <span>{t("当前显示历史图纸。源模型已变化，请重新生成。")}</span>
                    <EditorActionButton
                      label={t("更新图纸")}
                      icon="layers"
                      disabled={!canRun}
                      onClick={() => void submit("build")}
                    />
                  </div>
                )}
                {overlay && run && (
                  <div className="overlay-context">
                    <Icon name="chart" size={16} />
                    <strong>{t("结果叠加")}</strong>
                    <select
                      aria-label={t("叠加工况")}
                      value={runIndex}
                      onChange={(event) => chooseRun(Number(event.target.value))}
                    >
                      {runs.map((item, i) => (
                        <option key={item.path} value={i}>
                          {t(scenarioNames[item.result.scenario]) ?? item.result.scenario} /{" "}
                          {backendName(item.result)}
                        </option>
                      ))}
                    </select>
                    <span>{run.result.time_s[safeFrame]} s</span>
                    <span className={`tag ${run.current && drawing?.current ? "info" : "warning"}`}>
                      {run.current && drawing?.current
                        ? t("当前输入版本")
                        : t("历史结果 · 不叠加到图面")}
                    </span>
                    <EditorActionButton
                      label={t("完整分析")}
                      icon="chart"
                      onClick={() => openView("simulation")}
                    />
                    <EditorActionButton
                      className="overlay-dismiss"
                      label={t("退出结果叠加")}
                      icon="close"
                      onClick={() => {
                        setOverlay(false);
                        setPlayback((previous) => ({ ...previous, playing: false }));
                      }}
                    />
                  </div>
                )}
                {diagram ? (
                  <div
                    className={`canvas-viewport ${drawingCamera.panning ? "panning" : ""}`}
                    ref={viewport}
                    {...drawingCamera.handlers}
                  >
                    <div
                      className="drawing-world"
                      style={{ width: `${drawingCamera.width * zoom}px` }}
                    >
                      <div className="engineering-sheet">
                        <DiagramSvg
                          document={diagram}
                          selected={node?.id}
                          onSelect={(id) => {
                            const next = diagram.nodes.find((item) => item.id === id);
                            const object = next && instanceFromRef(source, next.semantic_ref);
                            if (object) choose({ kind: "instance", id: object });
                          }}
                          debug={debug}
                          temperatures={
                            overlay && run?.current && drawing.current
                              ? colors.temperatures
                              : undefined
                          }
                          flows={
                            overlay && run?.current && drawing.current ? colors.flows : undefined
                          }
                        />
                      </div>
                      <div className="canvas-caption">
                        <span>{t("连接视图 · 图面无物理比例")}</span>
                        <span>
                          {diagram.nodes.length}
                          {t("个设备 /")}
                          {diagram.edges.length}
                          {t("条连接")}
                        </span>
                      </div>
                    </div>
                  </div>
                ) : (
                  <Empty
                    title={t("从系统模型生成图纸")}
                    action={
                      <EditorActionButton
                        label={t("生成第一张图纸")}
                        icon="layers"
                        emphasis
                        disabled={!canRun}
                        onClick={() => void submit("build")}
                      />
                    }
                  >
                    {t("已读取")} {entries.length}
                    {t("个设备。生成系统图和原理图后，可以选择设备查看参数。")}
                  </Empty>
                )}
                {overlay && run && (
                  <div className="overlay-playback">
                    <EditorActionButton
                      label={playback.playing ? t("暂停") : t("播放")}
                      icon={playback.playing ? "pause" : "play"}
                      onClick={() =>
                        setPlayback((previous) => ({ ...previous, playing: !previous.playing }))
                      }
                    />
                    <input
                      type="range"
                      aria-label={t("叠加时间")}
                      min="0"
                      max={run.result.time_s.length - 1}
                      value={safeFrame}
                      onChange={(event) =>
                        setPlayback((previous) => ({
                          ...previous,
                          frame: Number(event.target.value),
                          playing: false,
                        }))
                      }
                    />
                    <output>{run.result.time_s[safeFrame]} s</output>
                  </div>
                )}
              </>
            )}
            {view === "equipment" && (
              <div className="data-view">
                <div className="view-intro">
                  <p>{t(info(view).description)}</p>
                  <EditorActionButton
                    label={t("导出设备表")}
                    icon="download"
                    onClick={exportEquipment}
                  />
                </div>
                <label className="library-search">
                  <Icon name="search" size={16} />
                  <input
                    aria-label={t("搜索设备")}
                    placeholder={t("搜索设备或类型…")}
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </label>
                <div className="table-summary">
                  <span>
                    {filtered.length} / {entries.length}
                    {t("个设备")}
                  </span>
                  <span>{t("点击设备编辑实例参数")}</span>
                </div>
                <div className="table-scroll">
                  <table className="equipment-table">
                    <thead>
                      <tr>
                        <th>{t("设备实例")}</th>
                        <th>{t("器件类型")}</th>
                        <th>{t("主要参数")}</th>
                        <th>{t("配置")}</th>
                        <th>{t("定位")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filtered.map(([id, item]) => {
                        const definition = source.components[item.component],
                          params = { ...definition.behavior?.parameters, ...item.parameters };
                        const key = [
                          "capacity_kw",
                          "nominal_load_kw",
                          "shutoff_head_m",
                          "ua_w_k",
                          "length_m",
                        ].find((key) => params[key]);
                        return (
                          <tr key={id} className={selected === id ? "selected" : ""}>
                            <td>
                              <button
                                className="object-link"
                                onClick={() => choose({ kind: "instance", id })}
                              >
                                <Icon
                                  name={item.component.includes("Pipe") ? "system" : "cube"}
                                  size={17}
                                />
                                <strong>{id}</strong>
                              </button>
                            </td>
                            <td>
                              <span>{t(names[item.component]) ?? item.component}</span>
                              <small>{item.component}</small>
                            </td>
                            <td>
                              {key ? (
                                <>
                                  <small>{t(parameterTitles[key]) ?? key}</small>
                                  <strong>{bindingValue(params[key], key)}</strong>
                                </>
                              ) : (
                                t("未定义")
                              )}
                            </td>
                            <td>
                              <span
                                className={`tag ${Object.keys(item.parameters ?? {}).length ? "info" : "neutral"}`}
                              >
                                {Object.keys(item.parameters ?? {}).length
                                  ? t("实例覆盖")
                                  : t("继承配置")}
                              </span>
                            </td>
                            <td>
                              <EditorActionButton
                                label={t("图中定位 {0}", { "0": id })}
                                icon="locate"
                                onClick={() => {
                                  if (choose({ kind: "instance", id }, false)) {
                                    openView("design");
                                    setLocateToken((previous) => previous + 1);
                                  }
                                }}
                              />
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  {!filtered.length && (
                    <Empty title={t("没有匹配设备")}>{t("调整设备搜索条件。")}</Empty>
                  )}
                </div>
              </div>
            )}
            {view === "components" && (
              <div className="data-view">
                <div className="view-intro">
                  <p>{t(info(view).description)}</p>
                  <span className="tag neutral">{t("类型只读")}</span>
                </div>
                <label className="library-search">
                  <Icon name="search" size={16} />
                  <input
                    aria-label={t("搜索器件类型")}
                    placeholder={t("搜索类型名称或标识…")}
                    value={librarySearch}
                    onChange={(event) => setLibrarySearch(event.target.value)}
                  />
                </label>
                <div className="component-list">
                  {types
                    .filter(([id]) =>
                      `${id} ${t(names[id]) ?? ""}`
                        .toLowerCase()
                        .includes(librarySearch.toLowerCase()),
                    )
                    .map(([id, definition]) => {
                      const representative = diagram?.nodes.find((node) => node.component === id),
                        graphic = representative
                          ? resolveGraphic(representative, "system")
                          : undefined;
                      const count = entries.filter(
                        ([, instance]) => instance.component === id,
                      ).length;
                      return (
                        <button
                          key={id}
                          className={`component-item ${selection?.kind === "type" && selection.id === id ? "selected" : ""}`}
                          onClick={() => choose({ kind: "type", id })}
                        >
                          <span className="component-preview">
                            {representative && graphic ? (
                              <svg
                                viewBox={`-20 -15 ${graphic.width + 40} ${graphic.height + 40}`}
                                aria-label={t(names[id]) ?? id}
                              >
                                <EngineeringSymbol node={{ ...representative, graphic }} />
                              </svg>
                            ) : (
                              <Icon name="cube" size={30} />
                            )}
                          </span>
                          <span className="component-identity">
                            <strong>{t(names[id]) ?? id}</strong>
                            <small>{id}</small>
                          </span>
                          <span className="component-facts">
                            {Object.keys(definition.ports ?? {}).length}
                            {t("个接口")}
                            <span>
                              {Object.keys(definition.behavior?.parameters ?? {}).length}
                              {t("个默认参数")}
                            </span>
                          </span>
                          <span className="tag neutral">
                            {count}
                            {t("个实例")}
                          </span>
                          <Icon name="chevron" size={16} />
                        </button>
                      );
                    })}
                  {!types.some(([id]) =>
                    `${id} ${t(names[id]) ?? ""}`
                      .toLowerCase()
                      .includes(librarySearch.toLowerCase()),
                  ) && <Empty title={t("没有匹配类型")}>{t("调整类型名称或标识。")}</Empty>}
                </div>
                <p className="section-note">
                  {t("此库展示当前模型内的类型。类型创建和性能模型编辑尚未接入。")}
                </p>
              </div>
            )}
            {view === "simulation" && (
              <div className="data-view simulation-view">
                <div className="simulation-config">
                  <label>
                    {t("运行工况")}
                    <select
                      aria-label={t("运行工况")}
                      value={scenario}
                      onChange={(event) => setScenario(event.target.value)}
                    >
                      <option value="all">{t("全部工况")}</option>
                      {data.scenarios.map((item) => (
                        <option key={item.id} value={item.id}>
                          {t(scenarioNames[item.id]) ?? item.id}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    {t("求解后端")}
                    <select
                      aria-label={t("求解后端")}
                      value={backend}
                      onChange={(event) => setBackend(event.target.value)}
                    >
                      <option value="scipy">{t("SciPy / 集总动态")}</option>
                      <option value="openmodelica">{t("OpenModelica / 外部求解器")}</option>
                    </select>
                  </label>
                  {busy && (
                    <span className="simulation-busy" role="status">
                      {t("任务运行中")}
                    </span>
                  )}
                  <EditorActionButton
                    label={t("运行仿真")}
                    icon="play"
                    emphasis
                    disabled={!canRun || !data.scenarios.length}
                    onClick={() => void submit("simulate")}
                  />
                </div>
                {!data.scenarios.length && (
                  <p className="section-note">{t("当前项目没有工况配置。")}</p>
                )}
                {run ? (
                  <>
                    <div className="analysis-heading">
                      <div>
                        <span className={`tag ${run.current ? "info" : "warning"}`}>
                          {run.current ? t("当前输入版本") : t("历史结果 · 源模型已变化")}
                        </span>
                        <span className={`tag ${run.result.status}`}>
                          {t("工程结论：")}
                          {t(labels[run.result.status]) ?? run.result.status}
                        </span>
                      </div>
                      <EditorActionButton
                        label={t("导出本次结果")}
                        icon="download"
                        onClick={() =>
                          void exportArtifact(
                            run.path,
                            `${run.result.scenario}-${backendName(run.result)}.json`,
                          )
                        }
                      />
                    </div>
                    {lastFocus && (
                      <div className="analysis-focus" role="status">
                        <Icon name="fit" size={16} />
                        <strong>{lastFocus}</strong>
                        <EditorActionButton
                          className="analysis-locate"
                          label={t("在图面查看")}
                          icon="locate"
                          onClick={() => {
                            openView("design");
                            setOverlay(true);
                            setLocateToken((previous) => previous + 1);
                          }}
                        />
                        <EditorActionButton
                          label={t("关闭定位提示")}
                          icon="close"
                          onClick={() => setLastFocus("")}
                        />
                      </div>
                    )}
                    {runPanel}
                    <div className="analysis-footnote">
                      <span>
                        {run.result.time_s.length}
                        {t("个采样点 ·")} {Object.keys(run.result.variables).length}
                        {t("个变量 ·")} {run.result.events.length}
                        {t("个事件")}
                      </span>
                      <span>{t("求解完成与工程约束通过分别记录。")}</span>
                    </div>
                  </>
                ) : (
                  <Empty title={t("选择工况，运行第一次仿真")}>
                    {t("求解结束后显示曲线、回放与约束问题。配置与源模型均按运行时输入保存。")}
                  </Empty>
                )}
                <details className="scenario-details">
                  <summary>
                    {t("工况配置 ·")}
                    {data.scenarios.length}
                    {t("个")}
                  </summary>
                  {data.scenarios.map((item) => (
                    <div key={item.id}>
                      <strong>{t(scenarioNames[item.id]) ?? item.id}</strong>
                      <span>
                        {item.duration_s} s · {item.events}
                        {t("个输入变化时刻")}
                      </span>
                      <code>{item.path}</code>
                    </div>
                  ))}
                </details>
              </div>
            )}
            {view === "checks" && (
              <div className="data-view">
                <div className="view-intro">
                  <p>{t(info(view).description)}</p>
                  <span>
                    {currentIssues.length}
                    {t("个当前问题")}
                  </span>
                </div>
                <div className="issue-filters">
                  <label>
                    <Icon name="search" size={15} />
                    <input
                      aria-label={t("搜索检查问题")}
                      placeholder={t("搜索设备、规则或工况…")}
                      value={issueSearch}
                      onChange={(event) => setIssueSearch(event.target.value)}
                    />
                  </label>
                  <select
                    aria-label={t("问题版本范围")}
                    value={issueScope}
                    onChange={(event) => setIssueScope(event.target.value)}
                  >
                    <option value="current">{t("当前输入")}</option>
                    <option value="historical">{t("历史输入")}</option>
                    <option value="all">{t("全部版本")}</option>
                  </select>
                </div>
                {visibleIssues.length ? (
                  <IssueRows issues={visibleIssues} locate={locateIssue} />
                ) : (
                  <div className="check-clear">
                    <Icon name="check" size={26} />
                    <div>
                      <strong>
                        {issueSearch ? t("没有匹配问题") : t("此范围内没有待处理问题")}
                      </strong>
                      <p>
                        {!data.reports.length && !runs.length
                          ? t("尚未运行模型检查或工况验证。")
                          : t("结果仅覆盖已运行的模型检查与工况约束，不代表全部工程条件已接受。")}
                      </p>
                    </div>
                  </div>
                )}
                <h3 className="section-heading">{t("检查记录")}</h3>
                <div className="report-list">
                  {data.reports.map((report) => (
                    <div key={report.path}>
                      <Icon name="check" size={18} />
                      <span>
                        <strong>
                          {report.kind === "studio-check.json"
                            ? t("模型静态检查")
                            : t("图纸构建检查")}
                        </strong>
                        <small>
                          {report.current ? t("当前输入版本") : t("历史输入版本")}
                          {report.data.files !== undefined &&
                            t(" · {0} 份文件", { "0": report.data.files })}
                        </small>
                      </span>
                      <span className={`tag ${report.data.status}`}>
                        {t(labels[report.data.status]) ?? report.data.status}
                      </span>
                      <EditorActionButton
                        label={t("导出报告")}
                        icon="download"
                        onClick={() => void exportArtifact(report.path, report.kind)}
                      />
                    </div>
                  ))}
                  {!data.reports.length && (
                    <p className="muted">{t("尚无模型检查报告，可在右上角运行模型检查。")}</p>
                  )}
                </div>
              </div>
            )}
            {view === "source" && (
              <div className="source-view">
                <div className="source-heading">
                  <code>{data.source_path}</code>
                  <span className="tag neutral">{t("只读")}</span>
                  <label>
                    <Icon name="search" size={15} />
                    <input
                      aria-label={t("查找源数据")}
                      placeholder={t("高亮匹配内容…")}
                      value={sourceSearch}
                      onChange={(event) => setSourceSearch(event.target.value)}
                    />
                  </label>
                </div>
                <div className="source-context">
                  {selection
                    ? t("已定位{0} {1}", {
                        "0": selection.kind === "type" ? t("类型") : t("实例"),
                        "1": selection.id,
                      })
                    : t("从左侧选择设备定位源数据")}{" "}
                  {t("· 参数保存会更新此文件")}
                </div>
                <div className="source-lines" ref={sourceViewport}>
                  {sourceLines.map((line, index) => (
                    <div
                      key={index}
                      data-line={index}
                      className={`${index === objectLine ? "source-selected" : ""} ${sourceSearch && line.toLowerCase().includes(sourceSearch.toLowerCase()) ? "source-match" : ""}`}
                    >
                      <span>{index + 1}</span>
                      <code>{line}</code>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {view === "outputs" && (
              <div className="data-view">
                <div className="view-intro">
                  <p>{t(info(view).description)}</p>
                  <span className="tag warning">{t("待工程审核")}</span>
                </div>
                <section className="output-section">
                  <h2>{t("设计资料")}</h2>
                  <div className="output-row">
                    <Icon name="code" size={20} />
                    <div>
                      <strong>{t("系统模型")}</strong>
                      <small>{data.source_path}</small>
                    </div>
                    <span className="tag info">{t("当前源数据")}</span>
                    <EditorActionButton
                      label={t("导出 JSON")}
                      icon="download"
                      onClick={() =>
                        download(JSON.stringify(source, null, 2) + "\n", "system.sdl.json")
                      }
                    />
                  </div>
                  <div className="output-row">
                    <Icon name="table" size={20} />
                    <div>
                      <strong>{t("设备表")}</strong>
                      <small>
                        {entries.length}
                        {t("个设备实例")}
                      </small>
                    </div>
                    <span className="tag info">{t("当前源数据")}</span>
                    <EditorActionButton
                      label={t("导出 CSV")}
                      icon="download"
                      onClick={exportEquipment}
                    />
                  </div>
                </section>
                <section className="output-section">
                  <h2>{t("图纸文件")}</h2>
                  {data.exports.map((item) => (
                    <div className="output-row" key={item.path}>
                      <Icon name="layers" size={20} />
                      <div>
                        <strong>{item.name}</strong>
                        <small title={item.path}>{item.path}</small>
                      </div>
                      <span className={`tag ${item.current ? "info" : "warning"}`}>
                        {item.current ? t("当前输入版本") : t("历史输入")}
                      </span>
                      <EditorActionButton
                        label={t("导出")}
                        icon="download"
                        onClick={() => void exportArtifact(item.path, item.name)}
                      />
                    </div>
                  ))}
                  {!data.exports.length && (
                    <p className="muted">{t("尚无图纸文件。点击“更新图纸”生成。")}</p>
                  )}
                </section>
                <section className="output-section">
                  <h2>{t("工况结果")}</h2>
                  {runs.map((item, index) => (
                    <div className="output-row" key={item.path}>
                      <Icon name="chart" size={20} />
                      <div>
                        <strong>
                          {t(scenarioNames[item.result.scenario]) ?? item.result.scenario} /{" "}
                          {backendName(item.result)}
                        </strong>
                        <small>
                          {item.current ? t("当前输入版本") : t("历史输入版本")}
                          {t("· 工程结论：")}
                          {t(labels[item.result.status]) ?? item.result.status}
                        </small>
                      </div>
                      <EditorActionButton
                        label={t("打开分析")}
                        icon="chart"
                        onClick={() => {
                          chooseRun(index);
                          openView("simulation");
                        }}
                      />
                      <EditorActionButton
                        label={t("导出 JSON")}
                        icon="download"
                        onClick={() =>
                          void exportArtifact(
                            item.path,
                            `${item.result.scenario}-${backendName(item.result)}.json`,
                          )
                        }
                      />
                    </div>
                  ))}
                  {!runs.length && (
                    <p className="muted">{t("尚无工况结果。前往“工况与结果”运行仿真。")}</p>
                  )}
                </section>
                <p className="section-note">
                  {t("导出保留当前文件的输入版本和工程状态。正式提资发布尚未接入。")}
                </p>
              </div>
            )}
            {view === "capabilities" && (
              <div className="data-view">
                <div className="view-intro">
                  <p>{t("HVAC 本地工作台 · 当前能力")}</p>
                  <span className="tag neutral">0.1.0</span>
                </div>
                <div className="capability-list">
                  {[
                    [
                      t("系统与实例参数"),
                      t("系统图、原理图、设备表、实例参数覆盖与恢复继承"),
                      t("已接入"),
                    ],
                    [
                      t("器件类型"),
                      t("查看项目内类型定义、默认参数、接口和使用实例；类型定义只读"),
                      t("有限支持"),
                    ],
                    [
                      t("仿真与约束"),
                      t("SciPy 动态求解、外部 OpenModelica、曲线与时间回放、约束定位"),
                      t("已接入"),
                    ],
                    [
                      t("检查与成果"),
                      t("模型检查、图纸构建、版本关联与历史结果标记、文件导出"),
                      t("已接入"),
                    ],
                    [t("拓扑与类型创建"), t("放置设备、编辑连接和创建器件类型"), t("未接入")],
                    [
                      t("架构搜索与物理布局"),
                      t("候选生成、优化求解、空间布局与物理管线路由"),
                      t("未接入"),
                    ],
                    [t("正式提资与协同"), t("提资发布、接收、跨专业采用及远程账户"), t("未接入")],
                  ].map(([title, description, status]) => (
                    <div key={title}>
                      <span>
                        <strong>{title}</strong>
                        <p>{description}</p>
                      </span>
                      <span className={`tag ${status === t("已接入") ? "pass" : "neutral"}`}>
                        {status}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </EditorArea>
      </WorkbenchSurface>
    </div>
  );
}

function HvacWorkbench(props: WorkbenchProps) {
  const data = props.data as Data;
  if (!data || data.error || !data.source)
    return (
      <WorkbenchSurface layout={props.layout} change={props.changeLayout}>
        <Empty title={t("无法读取暖通预览")}>
          {data?.error ?? t("当前项目没有可用的系统模型。")}
        </Empty>
      </WorkbenchSurface>
    );
  return <Workspace {...props} data={data} />;
}
const plugin: WorkbenchPlugin = {
  manifest: {
    id: "texsys:hvac",
    name: "Tex Studio HVAC",
    discipline: "暖通",
    version: "0.1.0",
    api_version: 1,
    capabilities: [
      "system-view",
      "equipment-table",
      "parameter-edit",
      "symbols",
      "source",
      "check",
      "build",
      "simulate",
      "results",
    ],
  },
  Workbench: HvacWorkbench,
};
export default plugin;
