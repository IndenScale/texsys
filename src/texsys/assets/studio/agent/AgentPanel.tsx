/** Right-side agent panel bound to the DeepSeek Harness kernel (DDR-0033, ADR-0035). */
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../ui";
import { NavigatorMenu, type NavigatorMenuItem } from "../NavigatorMenu";
import { t, localizeError, useLocale } from "../i18n";
import {
  applyAgentEvents,
  configChoices,
  contextWindowConfigOption,
  emptyTranscript,
  formatContextTokens,
  formatThinkingLabel,
  groupHistory,
  isModelOption,
  isReasoningOption,
  modelCatalog,
  reasoningConfigOption,
  relativeTime,
  sessionLabel,
  type AgentEventBatch,
  type AgentHistoryEntry,
  type AgentServiceState,
  type AgentSessionSummary,
  type ConfigOption,
  type NormalizedModelChoice,
  type Transcript,
} from "../agent-model";
import {
  Chip,
  Flyout,
  IconButton,
  MenuItem,
  MenuLabel,
  PickerButton,
  Popover,
  SendButton,
  Switch,
  UsageRing,
} from "./primitives";
import { TranscriptView } from "./Transcript";
import "./agent-ds.css";

const token =
  document.querySelector<HTMLMetaElement>('meta[name="tex-studio-token"]')?.content ?? "";

async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    signal,
    method: body === undefined ? "GET" : "POST",
    headers: {
      "X-Tex-Studio-Token": token,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(localizeError(data.error ?? `Request failed: ${response.status}`));
  return data as T;
}

const message = (cause: unknown) => String(cause instanceof Error ? cause.message : cause);
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const TABS_KEY = "texsys.agent.tabs";
const CONTEXT_KEY = "texsys.agent.includeActiveFile";
const PREFS_KEY = "texsys.agent.modelPrefs";
const MODEL_LABEL_KEY = "texsys.agent.modelName";
const MAX_TABS = 8;
const liveStates = new Set(["starting", "ready", "prompting"]);

function readStorage(key: string, storage: Storage | undefined = globalThis.localStorage) {
  try {
    return storage?.getItem(key) ?? undefined;
  } catch {
    return undefined;
  }
}

function writeStorage(
  key: string,
  value: string | undefined,
  storage: Storage | undefined = globalThis.localStorage,
) {
  try {
    if (value === undefined) storage?.removeItem(key);
    else storage?.setItem(key, value);
  } catch {
    // Preferences still work for the current page without storage.
  }
}

type Tab = { key: string; sessionId?: string };
type TabsState = { tabs: Tab[]; active: string };
type ModelPrefs = { model?: string; reasoning?: string };
type Pending = { text: string; context: string[] };

const newTab = (): Tab => ({
  key: `tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
});

function readTabs(workspace: string): TabsState {
  try {
    const raw = readStorage(`${TABS_KEY}.${workspace}`, globalThis.sessionStorage);
    const parsed = raw ? (JSON.parse(raw) as TabsState) : undefined;
    const tabs = (parsed?.tabs ?? [])
      .filter((tab) => typeof tab?.key === "string")
      .map((tab) => ({
        key: tab.key,
        sessionId: typeof tab.sessionId === "string" ? tab.sessionId : undefined,
      }))
      .slice(0, MAX_TABS);
    if (tabs.length > 0)
      return {
        tabs,
        active: tabs.some((tab) => tab.key === parsed?.active) ? parsed!.active : tabs[0].key,
      };
  } catch {
    // Fall through to a fresh draft tab.
  }
  const tab = newTab();
  return { tabs: [tab], active: tab.key };
}

function readPrefs(): ModelPrefs {
  try {
    const raw = readStorage(PREFS_KEY);
    return raw ? (JSON.parse(raw) as ModelPrefs) : {};
  } catch {
    return {};
  }
}

export type AgentPanelProps = {
  /** Workspace scope: bound project or standalone empty workspace. */
  workspace?: "project" | "empty";
  /** The right panel is open; refreshes the kernel state when shown. */
  visible: boolean;
  /** Whether the right panel is maximized across the editor stage. */
  maximized?: boolean;
  activeFile?: string;
  openFile?: (path: string, line?: number) => void;
  reviewChanges?: () => void;
  openProject?: () => void;
  onToggleMaximize?: () => void;
  onClose?: () => void;
};

type MenuState =
  | { kind: "mention"; query: string; start: number }
  | { kind: "command"; query: string }
  | undefined;

/** Hero empty state with brand slogan and task suggestions (DDR-0033 §4). */
function Hero({
  workspace,
  activeFile,
  unavailable,
  diagnostics,
  suggest,
}: {
  workspace: "project" | "empty";
  activeFile?: string;
  unavailable?: string;
  diagnostics: string[];
  suggest: (text: string) => void;
}) {
  const suggestions =
    workspace === "project"
      ? [
          ...(activeFile
            ? [
                {
                  icon: "file",
                  label: t("解释 {name} 的作用与结构", {
                    name: activeFile.split("/").at(-1) ?? activeFile,
                  }),
                },
              ]
            : []),
          { icon: "play", label: t("运行构建，解读失败的检查") },
          { icon: "branch", label: t("评估当前修改的影响范围") },
        ]
      : [
          { icon: "sparkles", label: t("梳理一个数据中心暖通方案的设计步骤") },
          { icon: "code", label: t("解释一段工程计算脚本") },
        ];
  return (
    <div className="ag-hero">
      <span className="ag-hero-mark" aria-hidden="true">
        <Icon name="logo" size={28} strokeWidth={1.6} />
      </span>
      <h2 className="ag-hero-title">
        <span>{t("看清系统")}</span>
        <span>
          <span className="ag-hero-accent">{t("验证")}</span>
          {t("每一次修改")}
        </span>
      </h2>
      <p className="ag-hero-text">
        {workspace === "project" ? (
          <>
            <span>{t("与你一起阅读工程、修改源文件")}</span>
            <span>{t("用构建与检查验证每一次结果")}</span>
          </>
        ) : (
          <>
            <span>{t("当前未打开项目，可直接提问讨论")}</span>
            <span>{t("智能体只在独立缓存目录中工作")}</span>
          </>
        )}
      </p>
      {unavailable ? (
        <div className="ag-hero-notice" role="alert">
          <strong>{t("DeepSeek Harness 不可用")}</strong>
          {unavailable}
          <br />
          {t("安装后重新打开面板：")}
          <code>npm install -g @deepseek-ai/dsh</code>
        </div>
      ) : (
        <div className="ag-suggestions">
          {suggestions.map((item) => (
            <button
              key={item.label}
              type="button"
              className="ag-suggestion"
              onClick={() => suggest(item.label)}
            >
              <Icon name={item.icon} size={14} />
              <span>{item.label}</span>
            </button>
          ))}
        </div>
      )}
      {diagnostics.length > 0 && (
        <div className="ag-hero-notice" role="status">
          {diagnostics.map((line) => (
            <div key={line}>{line}</div>
          ))}
        </div>
      )}
    </div>
  );
}

export function AgentPanel({
  workspace = "project",
  visible,
  maximized = false,
  activeFile,
  openFile,
  reviewChanges,
  openProject,
  onToggleMaximize,
  onClose,
}: AgentPanelProps) {
  const locale = useLocale();
  const [service, setService] = useState<AgentServiceState>();
  const [tabsState, setTabsState] = useState<TabsState>(() => readTabs(workspace));
  const [transcript, setTranscript] = useState<Transcript>(emptyTranscript);
  const [summary, setSummary] = useState<AgentSessionSummary>();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Record<string, Pending>>({});
  const [prefsApplied, setPrefsApplied] = useState<Set<string>>(() => new Set());
  const [includeActive, setIncludeActive] = useState(() => readStorage(CONTEXT_KEY) !== "false");
  const [extraContext, setExtraContext] = useState<string[]>([]);
  const [projectFiles, setProjectFiles] = useState<string[]>();
  const [autoApprove, setAutoApprove] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState("");
  const [atBottom, setAtBottom] = useState(true);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyFocus, setHistoryFocus] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState<string>();
  const [modelOpen, setModelOpen] = useState(false);
  const [modelHover, setModelHover] = useState<string>();
  const [modelEdit, setModelEdit] = useState(false);
  const [menu, setMenu] = useState<MenuState>();
  const [menuIndex, setMenuIndex] = useState(0);
  const [modelName, setModelName] = useState(() => readStorage(MODEL_LABEL_KEY) ?? "");

  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const input = useRef<HTMLTextAreaElement>(null);
  const historyBtn = useRef<HTMLButtonElement>(null);
  const modelBtn = useRef<HTMLButtonElement>(null);
  const modelPopover = useRef<HTMLDivElement | null>(null);
  const modelRows = useRef(new Map<string, HTMLButtonElement>());
  const hoverRowRef = useRef<HTMLElement | null>(null);
  const editRowRef = useRef<HTMLElement | null>(null);
  const cache = useRef(
    new Map<string, { transcript: Transcript; summary?: AgentSessionSummary; after: number }>(),
  );
  const autoApproved = useRef(new Set<string>());
  const lastThinking = useRef<string | undefined>(undefined);
  const validated = useRef(false);

  const { tabs, active } = tabsState;
  const activeTab = tabs.find((tab) => tab.key === active) ?? tabs[0];
  const sessionId = activeTab?.sessionId;
  const root = service?.root ?? "";
  const kernel = service?.kernel;
  const draft = drafts[active] ?? "";
  const state = summary?.state ?? (sessionId ? "starting" : undefined);
  const prompting = state === "prompting";
  const queued = sessionId ? pending[sessionId] : undefined;
  const busy = prompting || Boolean(queued);

  const updateTabs = useCallback(
    (update: (current: TabsState) => TabsState) => {
      setTabsState((current) => {
        const next = update(current);
        writeStorage(`${TABS_KEY}.${workspace}`, JSON.stringify(next), globalThis.sessionStorage);
        return next;
      });
    },
    [workspace],
  );

  const setDraft = useCallback(
    (value: string | ((current: string) => string)) =>
      setDrafts((current) => ({
        ...current,
        [active]: typeof value === "function" ? value(current[active] ?? "") : value,
      })),
    [active],
  );

  const loadService = useCallback(async () => {
    try {
      const next = await request<AgentServiceState>(
        `/api/agent?workspace=${encodeURIComponent(workspace)}`,
      );
      setService(next);
      return next;
    } catch (cause) {
      setError(message(cause));
      return undefined;
    }
  }, [workspace]);

  useEffect(() => {
    if (visible) void loadService();
  }, [visible, loadService]);

  // Tabs restored from the browser session only keep sessions the backend still runs.
  useEffect(() => {
    if (!service || validated.current) return;
    validated.current = true;
    const live = new Set(service.sessions.map((item) => item.id));
    updateTabs((current) => ({
      ...current,
      tabs: current.tabs.map((tab) =>
        tab.sessionId && !live.has(tab.sessionId) ? { key: tab.key } : tab,
      ),
    }));
  }, [service, updateTabs]);

  const detachSession = useCallback(
    (id: string) => {
      cache.current.delete(id);
      updateTabs((current) => ({
        ...current,
        tabs: current.tabs.map((tab) => (tab.sessionId === id ? { key: tab.key } : tab)),
      }));
    },
    [updateTabs],
  );

  // Long-poll the active session's ordered event log, resuming from the per-session cache.
  useEffect(() => {
    if (!sessionId || !root) {
      setTranscript(emptyTranscript());
      setSummary(undefined);
      return;
    }
    let stopped = false;
    const controller = new AbortController();
    const cached = cache.current.get(sessionId);
    let current = cached?.transcript ?? emptyTranscript();
    let after = cached?.after ?? 0;
    let lastState = cached?.summary?.state;
    setTranscript(current);
    setSummary(cached?.summary);
    void (async () => {
      let failures = 0;
      while (!stopped) {
        try {
          const batch = await request<AgentEventBatch>(
            `/api/agent/events?session=${encodeURIComponent(sessionId)}&after=${after}&wait=20`,
            undefined,
            controller.signal,
          );
          if (stopped) return;
          failures = 0;
          current = applyAgentEvents(current, batch.events, root, batch.reset);
          after = batch.seq;
          cache.current.set(sessionId, { transcript: current, summary: batch.session, after });
          setTranscript(current);
          setSummary(batch.session);
          if (batch.session.state !== lastState) {
            lastState = batch.session.state;
            void loadService();
          }
          if (batch.session.state === "closed") return;
        } catch {
          if (stopped) return;
          failures += 1;
          const latest = await loadService();
          if (latest && !latest.sessions.some((item) => item.id === sessionId)) {
            detachSession(sessionId);
            return;
          }
          await sleep(Math.min(5000, 800 * failures));
        }
      }
    })();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [sessionId, root, loadService, detachSession]);

  // Composer grows with content between two and eight lines.
  useLayoutEffect(() => {
    const element = input.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(160, Math.max(40, element.scrollHeight))}px`;
  }, [draft, visible, active]);

  useLayoutEffect(() => {
    const element = scroller.current;
    if (element && stick.current) {
      element.scrollTop = element.scrollHeight;
      setAtBottom(true);
    }
  }, [transcript, queued]);

  // Workbench re-reads files and Git after the agent touched the project.
  const announced = useRef(0);
  useEffect(() => {
    if (workspace !== "project") return;
    const changed = transcript.fileChanges > announced.current;
    announced.current = transcript.fileChanges;
    if (!changed) return;
    const timer = setTimeout(
      () => window.dispatchEvent(new Event("texsys:workspace:changed")),
      400,
    );
    return () => clearTimeout(timer);
  }, [workspace, transcript.fileChanges]);

  const models = useMemo(() => modelCatalog(transcript), [transcript]);
  const reasoning = useMemo(() => reasoningConfigOption(transcript), [transcript]);
  const contextOption = useMemo(() => contextWindowConfigOption(transcript), [transcript]);
  useEffect(() => {
    if (!models?.currentLabel || models.currentLabel === modelName) return;
    setModelName(models.currentLabel);
    writeStorage(MODEL_LABEL_KEY, models.currentLabel);
  }, [models?.currentLabel, modelName]);
  useEffect(() => {
    if (reasoning && reasoning.currentValue !== "off")
      lastThinking.current = reasoning.currentValue;
  }, [reasoning]);

  const setConfig = useCallback(
    async (option: ConfigOption, value: string) => {
      if (!sessionId) return;
      try {
        await request("/api/agent/config", { session: sessionId, config: option.id, value });
        const prefs = readPrefs();
        if (isModelOption(option)) prefs.model = value;
        else if (isReasoningOption(option)) prefs.reasoning = value;
        writeStorage(PREFS_KEY, JSON.stringify(prefs));
        setError("");
      } catch (cause) {
        setError(message(cause));
      }
    },
    [sessionId],
  );

  // Fresh sessions adopt the user's last model and thinking level once the kernel is ready.
  useEffect(() => {
    if (!sessionId || state !== "ready" || prefsApplied.has(sessionId)) return;
    const fresh = !transcript.items.some((item) => item.kind === "user");
    const prefs = readPrefs();
    const changes = fresh
      ? transcript.configOptions.flatMap((option) => {
          const wanted = isModelOption(option)
            ? prefs.model
            : isReasoningOption(option)
              ? prefs.reasoning
              : undefined;
          return wanted &&
            wanted !== option.currentValue &&
            configChoices(option).some((choice) => choice.value === wanted)
            ? [{ option, wanted }]
            : [];
        })
      : [];
    void (async () => {
      for (const { option, wanted } of changes) {
        try {
          await request("/api/agent/config", {
            session: sessionId,
            config: option.id,
            value: wanted,
          });
        } catch {
          // The kernel default stays in effect.
        }
      }
      setPrefsApplied((current) => new Set(current).add(sessionId));
    })();
  }, [sessionId, state, transcript.items, transcript.configOptions, prefsApplied]);

  // A queued first message waits for the kernel and the remembered model choice.
  useEffect(() => {
    if (!sessionId || !queued) return;
    if (state === "ready" && prefsApplied.has(sessionId)) {
      setPending(({ [sessionId]: _sent, ...rest }) => rest);
      void request("/api/agent/prompt", {
        session: sessionId,
        text: queued.text,
        context: queued.context,
      })
        .then(() => {
          setError("");
          void loadService();
        })
        .catch((cause) => {
          setError(message(cause));
          setDraft((value) => value || queued.text);
        });
    } else if (state && ["error", "exited", "closed"].includes(state)) {
      setPending(({ [sessionId]: _failed, ...rest }) => rest);
      setDraft((value) => value || queued.text);
    }
  }, [sessionId, queued, state, prefsApplied, loadService, setDraft]);

  // Session-scoped auto approval of tool permission requests.
  useEffect(() => {
    if (!sessionId || !autoApprove.has(sessionId)) return;
    for (const item of transcript.items) {
      if (item.kind !== "permission" || item.settled || autoApproved.current.has(item.requestKey))
        continue;
      const allow =
        item.options.find((option) => option.kind === "allow_once") ??
        item.options.find((option) => option.kind?.startsWith("allow"));
      if (!allow) continue;
      autoApproved.current.add(item.requestKey);
      void request("/api/agent/permission", {
        session: sessionId,
        key: item.requestKey,
        option: allow.optionId,
      }).catch((cause) => setError(message(cause)));
    }
  }, [sessionId, autoApprove, transcript.items]);

  const titleOf = useCallback(
    (tab: Tab) => {
      if (!tab.sessionId) return "";
      if (tab.key === active) return sessionLabel(summary) || (queued ? queued.text : "");
      return sessionLabel(service?.sessions.find((item) => item.id === tab.sessionId));
    },
    [active, summary, queued, service?.sessions],
  );

  const startSession = useCallback(async () => {
    const created = await request<AgentSessionSummary>("/api/agent/sessions", { workspace });
    const tabKey = active;
    updateTabs((current) => ({
      ...current,
      tabs: current.tabs.map((tab) =>
        tab.key === tabKey ? { ...tab, sessionId: created.id } : tab,
      ),
    }));
    void loadService();
    return created.id;
  }, [workspace, active, updateTabs, loadService]);

  const focusInput = () => requestAnimationFrame(() => input.current?.focus());

  const activate = useCallback(
    (key: string) => {
      updateTabs((current) => ({ ...current, active: key }));
      setExtraContext([]);
      setError("");
      setMenu(undefined);
      stick.current = true;
      focusInput();
    },
    [updateTabs],
  );

  const newSession = useCallback(() => {
    const blank = tabs.find((tab) => !titleOf(tab) && !(tab.sessionId && pending[tab.sessionId]));
    if (blank) {
      activate(blank.key);
      return;
    }
    if (tabs.length >= MAX_TABS) {
      setError(t("最多同时打开 {count} 个会话，请先关闭不用的会话。", { count: MAX_TABS }));
      return;
    }
    const tab = newTab();
    updateTabs((current) => ({ tabs: [...current.tabs, tab], active: tab.key }));
    setExtraContext([]);
    setError("");
    focusInput();
  }, [tabs, titleOf, pending, activate, updateTabs]);

  const closeTab = useCallback(
    (key: string) => {
      const tab = tabs.find((item) => item.key === key);
      if (tab?.sessionId) {
        const id = tab.sessionId;
        cache.current.delete(id);
        void request("/api/agent/close", { session: id })
          .catch(() => undefined)
          .finally(() => void loadService());
      }
      updateTabs((current) => {
        const index = current.tabs.findIndex((item) => item.key === key);
        const rest = current.tabs.filter((item) => item.key !== key);
        if (rest.length === 0) {
          const fresh = newTab();
          return { tabs: [fresh], active: fresh.key };
        }
        const nextActive =
          current.active === key ? rest[Math.min(index, rest.length - 1)].key : current.active;
        return { tabs: rest, active: nextActive };
      });
    },
    [tabs, updateTabs, loadService],
  );

  const openHistory = useCallback(
    async (entry: AgentHistoryEntry) => {
      setHistoryOpen(false);
      const existing = tabs.find((tab) => tab.sessionId === entry.id);
      if (existing) {
        activate(existing.key);
        return;
      }
      const reuse = activeTab && !activeTab.sessionId && !draft.trim() ? activeTab.key : undefined;
      if (!reuse && tabs.length >= MAX_TABS) {
        setError(t("最多同时打开 {count} 个会话，请先关闭不用的会话。", { count: MAX_TABS }));
        return;
      }
      try {
        await request<AgentSessionSummary>("/api/agent/resume", { session: entry.id, workspace });
        cache.current.delete(entry.id);
        setPrefsApplied((current) => new Set(current).add(entry.id));
        stick.current = true;
        const created = { ...newTab(), sessionId: entry.id };
        updateTabs((current) =>
          reuse
            ? {
                ...current,
                tabs: current.tabs.map((tab) =>
                  tab.key === reuse ? { ...tab, sessionId: entry.id } : tab,
                ),
                active: reuse,
              }
            : { tabs: [...current.tabs, created], active: created.key },
        );
        setError("");
        void loadService();
      } catch (cause) {
        setError(message(cause));
      }
    },
    [tabs, activeTab, draft, activate, workspace, updateTabs, loadService],
  );

  const deleteHistory = useCallback(
    async (id: string) => {
      setConfirmDelete(undefined);
      try {
        await request("/api/agent/history/delete", { session: id });
        cache.current.delete(id);
        updateTabs((current) => {
          const rest = current.tabs.filter((tab) => tab.sessionId !== id);
          if (rest.length === 0) {
            const fresh = newTab();
            return { tabs: [fresh], active: fresh.key };
          }
          return {
            tabs: rest,
            active: rest.some((tab) => tab.key === current.active) ? current.active : rest[0].key,
          };
        });
        void loadService();
      } catch (cause) {
        setError(message(cause));
      }
    },
    [updateTabs, loadService],
  );

  const attachedFiles = useMemo(() => {
    if (workspace !== "project") return [];
    const list: string[] = [];
    if (includeActive && activeFile) list.push(activeFile);
    for (const file of extraContext) if (!list.includes(file) && list.length < 16) list.push(file);
    return list;
  }, [workspace, includeActive, activeFile, extraContext]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || busy || kernel?.available === false) return;
    const context = attachedFiles;
    setDraft("");
    setExtraContext([]);
    setMenu(undefined);
    stick.current = true;
    setAtBottom(true);
    try {
      let id = sessionId;
      if (!id || !state || !liveStates.has(state)) {
        if (id) void request("/api/agent/close", { session: id }).catch(() => undefined);
        id = await startSession();
      }
      setPending((current) => ({ ...current, [id!]: { text, context } }));
      setError("");
    } catch (cause) {
      setDraft(text);
      setError(message(cause));
    }
  }, [draft, busy, kernel?.available, attachedFiles, setDraft, sessionId, state, startSession]);

  const stop = useCallback(() => {
    if (!sessionId) return;
    if (queued) {
      setPending(({ [sessionId]: _dropped, ...rest }) => rest);
      setDraft((value) => value || queued.text);
      return;
    }
    void request("/api/agent/cancel", { session: sessionId }).catch((cause) =>
      setError(message(cause)),
    );
  }, [sessionId, queued, setDraft]);

  const respond = (key: string, option: string | null) =>
    sessionId &&
    void request("/api/agent/permission", { session: sessionId, key, option }).catch((cause) =>
      setError(message(cause)),
    );

  // @ file mentions and / commands --------------------------------------------------------------

  const loadProjectFiles = useCallback(() => {
    if (workspace !== "project" || projectFiles) return;
    void request<{ files?: (string | { path: string })[] }>("/api/files?compact=1")
      .then((response) =>
        setProjectFiles(
          (response.files ?? [])
            .map((entry) => (typeof entry === "string" ? entry : entry.path))
            .filter(Boolean),
        ),
      )
      .catch(() => setProjectFiles([]));
  }, [workspace, projectFiles]);

  const updateMenu = (value: string, caret: number) => {
    const before = value.slice(0, caret);
    const mention = workspace === "project" ? /(?:^|\s)@([^\s@]*)$/.exec(before) : null;
    if (mention) {
      loadProjectFiles();
      setMenu({ kind: "mention", query: mention[1], start: caret - mention[1].length - 1 });
      setMenuIndex(0);
    } else if (/^\/[^\s]*$/.test(before) && !value.includes("\n")) {
      setMenu({ kind: "command", query: before.slice(1) });
      setMenuIndex(0);
    } else setMenu(undefined);
  };

  const menuItems = useMemo(() => {
    if (!menu) return [];
    const query = menu.query.toLowerCase();
    if (menu.kind === "command")
      return transcript.commands
        .filter((command) => command.name.toLowerCase().startsWith(query))
        .slice(0, 8)
        .map((command) => ({
          key: command.name,
          label: `/${command.name}`,
          detail: command.description ?? "",
        }));
    return (projectFiles ?? [])
      .filter((path) => !attachedFiles.includes(path) && path.toLowerCase().includes(query))
      .sort((a, b) => {
        const an = (a.split("/").at(-1) ?? a).toLowerCase().startsWith(query) ? 0 : 1;
        const bn = (b.split("/").at(-1) ?? b).toLowerCase().startsWith(query) ? 0 : 1;
        return an - bn || a.length - b.length;
      })
      .slice(0, 30)
      .map((path) => ({ key: path, label: path.split("/").at(-1) ?? path, detail: path }));
  }, [menu, transcript.commands, projectFiles, attachedFiles]);

  const chooseMenuItem = (key: string) => {
    if (!menu) return;
    if (menu.kind === "command") setDraft(`/${key} `);
    else {
      const caret = input.current?.selectionStart ?? draft.length;
      setDraft(draft.slice(0, menu.start) + draft.slice(caret));
      setExtraContext((current) =>
        current.includes(key) ? current : [...current, key].slice(0, 16),
      );
    }
    setMenu(undefined);
    focusInput();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (menu && menuItems.length > 0) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const step = event.key === "ArrowDown" ? 1 : -1;
        setMenuIndex((index) => (index + step + menuItems.length) % menuItems.length);
        return;
      }
      if ((event.key === "Enter" && !event.shiftKey) || event.key === "Tab") {
        event.preventDefault();
        chooseMenuItem(menuItems[Math.min(menuIndex, menuItems.length - 1)].key);
        return;
      }
    }
    if (menu && event.key === "Escape") {
      event.preventDefault();
      setMenu(undefined);
      return;
    }
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send();
    } else if (event.key === "Escape" && busy) {
      event.preventDefault();
      stop();
    }
  };

  // Model menu ------------------------------------------------------------------------------------

  const toggleModel = () => {
    if (modelOpen) {
      setModelOpen(false);
      return;
    }
    setModelHover(undefined);
    setModelEdit(false);
    setModelOpen(true);
    if (!sessionId && kernel?.available)
      void startSession().catch((cause) => setError(message(cause)));
  };

  const modelOption = transcript.configOptions.find(isModelOption);
  const groups = models?.groups ?? [];
  const hovered = modelHover
    ? models?.choices.find((choice) => choice.value === modelHover)
    : undefined;
  const thinkingChoices = reasoning
    ? configChoices(reasoning).filter((choice) => choice.value !== "off")
    : [];
  const thinkingOn = Boolean(reasoning && reasoning.currentValue !== "off");
  const contextLabel = transcript.usage?.size
    ? formatContextTokens(transcript.usage.size)
    : undefined;

  const modelDetail = (choice: NormalizedModelChoice) => {
    const current = choice.value === models?.currentValue;
    const rows: [string, string][] = [];
    if (choice.group) rows.push([t("提供方"), choice.group]);
    if (current && reasoning)
      rows.push([t("思考模式"), formatThinkingLabel(reasoning.currentValue, locale)]);
    if (current && contextLabel) rows.push([t("上下文窗口"), contextLabel]);
    return (
      <>
        <strong className="ag-flyout-title">{choice.name}</strong>
        {choice.description && <p className="ag-flyout-text">{choice.description}</p>}
        {rows.length > 0 && (
          <dl className="ag-flyout-props">
            {rows.map(([label, value]) => (
              <React.Fragment key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </React.Fragment>
            ))}
          </dl>
        )}
      </>
    );
  };

  // History ---------------------------------------------------------------------------------------

  const history = service?.history ?? [];
  const historyMatches = useMemo(() => {
    const query = historyQuery.trim().toLowerCase();
    return query ? history.filter((entry) => entry.title.toLowerCase().includes(query)) : history;
  }, [history, historyQuery]);
  const historyGroups = useMemo(() => groupHistory(historyMatches), [historyMatches]);
  const historyFlat = historyGroups.flatMap((group) => group.entries);
  const openIds = new Set(tabs.flatMap((tab) => (tab.sessionId ? [tab.sessionId] : [])));

  const timeLabel = (seconds: number) => {
    const value = relativeTime(seconds);
    return t(value.key, value.values);
  };

  // Header actions --------------------------------------------------------------------------------

  const moreItems: NavigatorMenuItem[] = [
    ...(workspace === "project" && reviewChanges
      ? [{ label: t("在源代码管理中审查修改"), icon: "branch", run: reviewChanges }]
      : []),
    ...(workspace === "empty" && openProject
      ? [{ label: t("打开已启动的项目"), icon: "grid", run: openProject }]
      : []),
    {
      label: t("本会话自动批准工具授权"),
      checkbox: true,
      checked: Boolean(sessionId && autoApprove.has(sessionId)),
      separator: true,
      reason: sessionId ? undefined : t("发送消息后可设置。"),
      run: sessionId
        ? () =>
            setAutoApprove((current) => {
              const next = new Set(current);
              if (next.has(sessionId)) next.delete(sessionId);
              else next.add(sessionId);
              return next;
            })
        : undefined,
    },
    ...(onToggleMaximize
      ? [
          {
            label: t(maximized ? "还原面板大小" : "最大化面板大小"),
            icon: maximized ? "panel-restore" : "panel-maximize",
            separator: true,
            run: onToggleMaximize,
          },
        ]
      : []),
    ...(onClose ? [{ label: t("关闭智能体面板"), icon: "close", run: onClose }] : []),
  ];

  const hasConversation = transcript.items.length > 0 || Boolean(queued);
  const working = busy && (Boolean(queued) || transcript.items.at(-1)?.kind === "user");
  const canSend = Boolean(draft.trim()) && kernel?.available !== false;
  const unavailable =
    kernel && !kernel.available ? kernel.reason || t("未找到 dsh 命令。") : undefined;
  const placeholder = busy
    ? t("智能体正在处理…")
    : workspace === "project"
      ? t("规划与修改，@ 添加上下文，/ 使用命令")
      : t("提问或讨论方案，/ 使用命令");

  return (
    <div className="agent-panel" data-workspace={workspace}>
      <div className="ag-tabbar" translate="no">
        <div
          className="ag-tabs"
          role="tablist"
          aria-label={t("会话")}
          onKeyDown={(event) => {
            if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
            const index = tabs.findIndex((tab) => tab.key === active);
            const next =
              tabs[(index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
            activate(next.key);
          }}
        >
          {tabs.map((tab) => {
            const title = titleOf(tab) || t("新会话");
            const selected = tab.key === active;
            const tabBusy = selected
              ? busy
              : Boolean(
                  (tab.sessionId && pending[tab.sessionId]) ||
                  service?.sessions.find((item) => item.id === tab.sessionId)?.state ===
                    "prompting",
                );
            return (
              <div
                key={tab.key}
                role="tab"
                tabIndex={selected ? 0 : -1}
                aria-selected={selected}
                className="ag-tab"
                title={title}
                onClick={() => activate(tab.key)}
                onAuxClick={(event) => event.button === 1 && closeTab(tab.key)}
              >
                <span className="ag-tab-icon">
                  <Icon name="chat" size={14} />
                  {tabBusy && <span className="ag-tab-busy" aria-label={t("处理中")} />}
                </span>
                <span className="ag-tab-title">{title}</span>
                <span
                  role="button"
                  tabIndex={-1}
                  className="ag-tab-close"
                  aria-label={t("关闭会话 {name}", { name: title })}
                  title={t("关闭会话")}
                  onClick={(event) => {
                    event.stopPropagation();
                    closeTab(tab.key);
                  }}
                >
                  <Icon name="close" size={12} />
                </span>
              </div>
            );
          })}
        </div>
        <div className="ag-tabbar-actions">
          <IconButton label={t("新建会话")} icon="plus" onClick={newSession} />
          <IconButton
            label={t("对话历史")}
            icon="history"
            buttonRef={historyBtn}
            aria-expanded={historyOpen}
            onClick={() => {
              setHistoryOpen((value) => !value);
              setHistoryQuery("");
              setHistoryFocus(0);
              setConfirmDelete(undefined);
              void loadService();
            }}
          />
          <NavigatorMenu
            label={t("更多智能体操作")}
            title={t("更多操作…")}
            items={moreItems}
            placement="end"
            direction="down"
          />
        </div>
      </div>

      <div className="ag-body">
        <div
          ref={scroller}
          className="ag-scroll"
          onScroll={(event) => {
            const element = event.currentTarget;
            const bottom = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
            stick.current = bottom;
            setAtBottom(bottom);
          }}
        >
          {hasConversation ? (
            <div className="ag-transcript" aria-live="polite">
              <TranscriptView
                transcript={transcript}
                root={root}
                prompting={prompting}
                openFile={workspace === "project" ? openFile : undefined}
                respond={respond}
              />
              {queued && (
                <div className="ag-user" style={{ opacity: 0.7 }}>
                  <p>{queued.text}</p>
                </div>
              )}
              {working && (
                <div className="ag-pending" role="status">
                  <span className="ag-spinner" aria-hidden="true" />
                  {queued && state !== "ready" ? t("正在启动 DeepSeek Harness…") : t("正在处理…")}
                </div>
              )}
            </div>
          ) : (
            <Hero
              workspace={workspace}
              activeFile={activeFile}
              unavailable={unavailable}
              diagnostics={service?.diagnostics ?? []}
              suggest={(text) => {
                setDraft(text);
                focusInput();
              }}
            />
          )}
        </div>
        {!atBottom && hasConversation && (
          <IconButton
            className="ag-to-bottom"
            label={t("滚动到底部")}
            icon="arrow-down"
            size={14}
            onClick={() => {
              const element = scroller.current;
              if (!element) return;
              stick.current = true;
              element.scrollTop = element.scrollHeight;
              setAtBottom(true);
            }}
          />
        )}
      </div>

      <div className="ag-composer">
        {workspace === "project" && transcript.changed.length > 0 && (
          <details className="ag-working-set">
            <summary>
              <Icon name="files" size={14} />
              <span>{t("智能体修改了 {count} 个文件", { count: transcript.changed.length })}</span>
              {reviewChanges && (
                <button
                  type="button"
                  className="ag-link"
                  onClick={(event) => {
                    event.preventDefault();
                    reviewChanges();
                  }}
                >
                  {t("审查修改")}
                </button>
              )}
            </summary>
            <ul>
              {transcript.changed.map((path) => (
                <li key={path}>
                  <button type="button" title={path} onClick={() => openFile?.(path)}>
                    {path}
                  </button>
                </li>
              ))}
            </ul>
          </details>
        )}
        <div className="ag-composer-main">
          {workspace === "project" && (activeFile || extraContext.length > 0) && (
            <div className="ag-chips" translate="no">
              {activeFile && (
                <Chip
                  label={activeFile.split("/").at(-1) ?? activeFile}
                  title={t(
                    includeActive ? "当前文件 {path} 已附带，点击取消" : "点击附带当前文件 {path}",
                    {
                      path: activeFile,
                    },
                  )}
                  off={!includeActive}
                  onClick={() => {
                    writeStorage(CONTEXT_KEY, String(!includeActive));
                    setIncludeActive(!includeActive);
                  }}
                />
              )}
              {extraContext
                .filter((path) => path !== activeFile)
                .map((path) => (
                  <Chip
                    key={path}
                    label={path.split("/").at(-1) ?? path}
                    title={path}
                    onRemove={() =>
                      setExtraContext((current) => current.filter((item) => item !== path))
                    }
                  />
                ))}
            </div>
          )}
          <textarea
            ref={input}
            className="ag-input"
            rows={2}
            value={draft}
            placeholder={placeholder}
            aria-label={t("向智能体发送消息")}
            disabled={kernel?.available === false}
            onChange={(event) => {
              setDraft(event.target.value);
              updateMenu(
                event.target.value,
                event.target.selectionStart ?? event.target.value.length,
              );
            }}
            onKeyDown={onKeyDown}
            onBlur={() => setTimeout(() => setMenu(undefined), 120)}
          />
          <div className="ag-composer-toolbar" translate="no">
            <div className="ag-composer-toolbar-start">
              <PickerButton
                buttonRef={modelBtn}
                label={models?.currentLabel || modelName || "DeepSeek"}
                title={t("选择模型")}
                expanded={modelOpen}
                disabled={kernel?.available === false}
                onClick={toggleModel}
              />
            </div>
            <div className="ag-composer-toolbar-end">
              {transcript.usage && transcript.usage.size > 0 && (
                <UsageRing used={transcript.usage.used} size={transcript.usage.size} />
              )}
              <SendButton
                busy={busy}
                disabled={!canSend}
                onSend={() => void send()}
                onStop={stop}
              />
            </div>
          </div>
        </div>
        {error && (
          <p className="ag-composer-error" role="alert">
            {error}
          </p>
        )}
      </div>

      <Popover
        anchorRef={historyBtn}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        side="below"
        align="end"
        label={t("对话历史")}
        className="ag-history"
      >
        <div className="ag-history-head">{t("对话历史")}</div>
        <input
          className="ag-search"
          autoFocus
          value={historyQuery}
          placeholder={history.length ? t("搜索对话历史") : t("暂无对话历史")}
          aria-label={t("搜索对话历史")}
          disabled={!history.length}
          onChange={(event) => {
            setHistoryQuery(event.target.value);
            setHistoryFocus(0);
          }}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              const step = event.key === "ArrowDown" ? 1 : -1;
              setHistoryFocus(
                (index) => (index + step + historyFlat.length) % Math.max(1, historyFlat.length),
              );
            } else if (event.key === "Enter" && historyFlat[historyFocus]) {
              event.preventDefault();
              void openHistory(historyFlat[historyFocus]);
            }
          }}
        />
        {history.length > 0 && (
          <div className="ag-popover-scroll" role="listbox" aria-label={t("对话历史")}>
            {historyFlat.length === 0 && <div className="ag-menu-empty">{t("没有匹配的对话")}</div>}
            {historyGroups.map((group) => (
              <div key={group.key} role="group" aria-label={t(group.label)}>
                <MenuLabel>{t(group.label)}</MenuLabel>
                {group.entries.map((entry) =>
                  confirmDelete === entry.id ? (
                    <div key={entry.id} className="ag-history-confirm">
                      <span>{t("删除此记录？")}</span>
                      <button
                        type="button"
                        className="ag-button is-danger"
                        onClick={() => void deleteHistory(entry.id)}
                      >
                        {t("删除")}
                      </button>
                      <button
                        type="button"
                        className="ag-button"
                        onClick={() => setConfirmDelete(undefined)}
                      >
                        {t("取消")}
                      </button>
                    </div>
                  ) : (
                    <div
                      key={entry.id}
                      className={`ag-history-row${openIds.has(entry.id) ? " is-open" : ""}`}
                    >
                      <MenuItem
                        label={entry.title || t("未命名会话")}
                        title={entry.title}
                        detail={timeLabel(entry.updated_at || entry.created_at)}
                        focused={historyFlat[historyFocus]?.id === entry.id}
                        onMouseEnter={() => setHistoryFocus(historyFlat.indexOf(entry))}
                        onClick={() => void openHistory(entry)}
                      />
                      <IconButton
                        className="ag-history-delete"
                        label={t("删除对话记录")}
                        icon="trash"
                        size={13}
                        onClick={() => setConfirmDelete(entry.id)}
                      />
                    </div>
                  ),
                )}
              </div>
            ))}
          </div>
        )}
      </Popover>

      <Popover
        anchorRef={modelBtn}
        open={modelOpen}
        onClose={() => setModelOpen(false)}
        onEscape={() => {
          if (!modelEdit) return false;
          setModelEdit(false);
          return true;
        }}
        side="above"
        label={t("选择模型")}
        className="ag-model-menu"
        popoverRef={modelPopover}
      >
        <div
          className="ag-popover-scroll"
          role="listbox"
          aria-label={t("模型")}
          onMouseLeave={() => setModelHover(undefined)}
        >
          {!models && (
            <div className="ag-menu-empty">
              {state === "error" || unavailable
                ? t("暂时无法读取模型列表。")
                : t("正在读取模型列表…")}
            </div>
          )}
          {groups.map((group) => (
            <div key={group.key} role="group" aria-label={group.label}>
              {groups.length > 1 && <MenuLabel>{group.label}</MenuLabel>}
              {group.options.map((choice) => {
                const current = choice.value === models?.currentValue;
                return (
                  <MenuItem
                    key={choice.value}
                    rowRef={(node) => {
                      if (node) modelRows.current.set(choice.value, node);
                      else modelRows.current.delete(choice.value);
                    }}
                    label={choice.name}
                    current={current}
                    onMouseEnter={(event) => {
                      if (modelEdit) return;
                      hoverRowRef.current = event.currentTarget;
                      setModelHover(choice.value);
                    }}
                    onClick={() => {
                      if (!current && modelOption) void setConfig(modelOption, choice.value);
                      setModelOpen(false);
                    }}
                    trailing={
                      current && (reasoning || contextOption) ? (
                        <IconButton
                          label={t("模型设置")}
                          icon="edit"
                          size={13}
                          active={modelEdit}
                          onClick={(event) => {
                            event.stopPropagation();
                            editRowRef.current = modelRows.current.get(choice.value) ?? null;
                            setModelHover(undefined);
                            setModelEdit((value) => !value);
                          }}
                        />
                      ) : undefined
                    }
                  />
                );
              })}
            </div>
          ))}
        </div>
      </Popover>
      {modelOpen && hovered && !modelEdit && (
        <Flyout popoverRef={modelPopover} rowRef={hoverRowRef} label={t("模型说明")}>
          {modelDetail(hovered)}
        </Flyout>
      )}
      {modelOpen && modelEdit && models && (
        <Flyout popoverRef={modelPopover} rowRef={editRowRef} label={t("模型设置")} settings>
          <strong className="ag-flyout-title">{models.currentLabel}</strong>
          {contextOption && (
            <>
              <div className="ag-flyout-section">{t("上下文窗口")}</div>
              {configChoices(contextOption).map((choice) => (
                <MenuItem
                  key={choice.value}
                  label={formatContextTokens(choice.name) || choice.name}
                  checked={choice.value === contextOption.currentValue}
                  onClick={() => void setConfig(contextOption, choice.value)}
                />
              ))}
            </>
          )}
          {reasoning && (
            <>
              {contextOption && <div className="ag-menu-separator" />}
              <div className="ag-flyout-section">
                <span>{t("思考模式")}</span>
                {configChoices(reasoning).some((choice) => choice.value === "off") && (
                  <Switch
                    label={t("思考模式")}
                    checked={thinkingOn}
                    onChange={(on) => {
                      const fallback =
                        lastThinking.current &&
                        thinkingChoices.some((choice) => choice.value === lastThinking.current)
                          ? lastThinking.current
                          : (
                              thinkingChoices.find((choice) => choice.value === "high") ??
                              thinkingChoices[0]
                            )?.value;
                      const value = on ? fallback : "off";
                      if (value) void setConfig(reasoning, value);
                    }}
                  />
                )}
              </div>
              {thinkingChoices.map((choice) => (
                <MenuItem
                  key={choice.value}
                  label={formatThinkingLabel(choice.name || choice.value, locale)}
                  title={choice.description}
                  checked={choice.value === reasoning.currentValue}
                  disabled={
                    !thinkingOn && configChoices(reasoning).some((item) => item.value === "off")
                  }
                  onClick={() => void setConfig(reasoning, choice.value)}
                />
              ))}
            </>
          )}
        </Flyout>
      )}

      <Popover
        anchorRef={input}
        open={Boolean(menu && menuItems.length > 0)}
        onClose={() => setMenu(undefined)}
        side="above"
        label={menu?.kind === "command" ? t("命令") : t("添加上下文")}
        role="listbox"
        className="ag-mention-menu"
      >
        <div className="ag-popover-scroll">
          <MenuLabel>{menu?.kind === "command" ? t("命令") : t("添加上下文")}</MenuLabel>
          {menuItems.map((item, index) => (
            <MenuItem
              key={item.key}
              icon={menu?.kind === "command" ? "code" : "file"}
              label={item.label}
              detail={item.detail}
              title={item.detail}
              focused={index === menuIndex}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setMenuIndex(index)}
              onClick={() => chooseMenuItem(item.key)}
            />
          ))}
        </div>
      </Popover>
    </div>
  );
}
