import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Icon } from "./ui";
import { NavigatorButton } from "./NavigatorPanel";
import { NavigatorMenu, type NavigatorMenuItem } from "./NavigatorMenu";
import { MarkdownPreview } from "./MarkdownPreview";
import { t, localizeError } from "./i18n";
import {
  applyAgentEvents,
  configChoices,
  diffStats,
  emptyTranscript,
  lineDiff,
  presentTool,
  sessionLabel,
  type AgentEventBatch,
  type AgentInfo,
  type AgentServiceState,
  type AgentSessionSummary,
  type ConfigOption,
  type TerminalRecord,
  type Transcript,
  type TranscriptItem,
} from "./agent-model";
import "./agent-panel.css";

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

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const SESSION_KEY = "texsys.agent.session";
const AGENT_KEY = "texsys.agent.default";
const CONTEXT_KEY = "texsys.agent.includeActiveFile";
const liveStates = new Set(["starting", "ready", "prompting", "auth_required"]);

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
    // Agent preferences still work for the current page without storage.
  }
}

export type AgentPanelProps = {
  /** A project is bound and the agent may work in it. */
  enabled: boolean;
  /** The agent view is showing; the service list refreshes when it becomes visible. */
  visible: boolean;
  activeFile?: string;
  openFile?: (path: string, line?: number) => void;
  reviewChanges?: () => void;
  openProject?: () => void;
};

function DiffView({ oldText, newText }: { oldText: string; newText: string }) {
  const lines = useMemo(() => lineDiff(oldText, newText), [oldText, newText]);
  return (
    <pre className="agent-diff" aria-label={t("差异")}>
      {lines.map((line, index) =>
        line.type === "gap" ? (
          <span key={index} className="agent-diff-gap">
            ⋯
          </span>
        ) : (
          <span key={index} className={`agent-diff-${line.type}`}>
            <i aria-hidden="true">{line.type === "add" ? "+" : line.type === "del" ? "−" : " "}</i>
            {line.text || " "}
          </span>
        ),
      )}
    </pre>
  );
}

function DiffSummary({
  path,
  oldText,
  newText,
  partial,
  created,
  openFile,
}: {
  path: string;
  oldText: string;
  newText: string;
  partial?: boolean;
  created?: boolean;
  openFile?: (path: string) => void;
}) {
  const stats = useMemo(() => diffStats(lineDiff(oldText, newText, 0)), [oldText, newText]);
  return (
    <details className="agent-diff-block">
      <summary>
        <Icon name="chevron-right" size={12} />
        <span className="agent-path" title={path}>
          {path.split("/").at(-1)}
        </span>
        {created && <span className="agent-tag">{t("新建")}</span>}
        {partial && <span className="agent-tag">{t("片段")}</span>}
        <span className="agent-diff-stat">
          <ins>+{stats.added}</ins> <del>−{stats.removed}</del>
        </span>
        {openFile && !path.startsWith("/") && (
          <button
            type="button"
            className="agent-link-button"
            onClick={(event) => {
              event.preventDefault();
              openFile(path);
            }}
          >
            {t("打开")}
          </button>
        )}
      </summary>
      <DiffView oldText={oldText} newText={newText} />
    </details>
  );
}

function TerminalView({ terminal }: { terminal?: TerminalRecord }) {
  if (!terminal) return null;
  const exit = terminal.exitStatus;
  return (
    <div className="agent-terminal">
      <div className="agent-terminal-head">
        <Icon name="terminal" size={12} />
        <code>{terminal.command}</code>
        {exit && (
          <span className={exit.exitCode === 0 ? "agent-ok" : "agent-bad"}>
            {exit.signal ? exit.signal : t("退出码 {code}", { code: String(exit.exitCode) })}
          </span>
        )}
      </div>
      {terminal.output && (
        <pre>
          {terminal.truncated ? "…\n" : ""}
          {terminal.output.length > 12000 ? "…" + terminal.output.slice(-12000) : terminal.output}
        </pre>
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: string }) {
  if (status === "in_progress" || status === "pending")
    return <span className="agent-spinner" role="img" aria-label={t("进行中")} />;
  if (status === "failed") return <Icon name="error-circle" size={14} />;
  return <Icon name="tick" size={14} />;
}

function ToolCard({
  item,
  root,
  terminals,
  openFile,
}: {
  item: Extract<TranscriptItem, { kind: "tool" }>;
  root: string;
  terminals: Record<string, TerminalRecord>;
  openFile?: (path: string, line?: number) => void;
}) {
  const view = presentTool(item, root);
  const hasBody = view.diffs.length > 0 || view.text.length > 0 || view.terminals.length > 0;
  const head = (
    <>
      <span className={`agent-tool-status is-${item.status}`}>
        <StatusIcon status={item.status} />
      </span>
      <Icon name={view.icon} size={14} />
      <span className="agent-tool-title">{view.title}</span>
      {view.detail &&
        (view.path && openFile && !view.command ? (
          <button
            type="button"
            className="agent-tool-detail agent-link-button"
            title={view.path}
            onClick={(event) => {
              event.preventDefault();
              openFile(view.path!, view.line);
            }}
          >
            {view.path}
            {view.line ? `:${view.line}` : ""}
          </button>
        ) : (
          <code className="agent-tool-detail" title={view.detail}>
            {view.detail}
          </code>
        ))}
    </>
  );
  if (!hasBody) return <div className={`agent-tool is-${item.status}`}>{head}</div>;
  return (
    <details className={`agent-tool is-${item.status}`} open={item.status === "failed"}>
      <summary>{head}</summary>
      <div className="agent-tool-body">
        {view.diffs.map((diff, index) => (
          <DiffSummary key={index} {...diff} openFile={openFile} />
        ))}
        {view.terminals.map((id) => (
          <TerminalView key={id} terminal={terminals[id]} />
        ))}
        {view.text.map((value, index) => (
          <pre key={index} className="agent-tool-output">
            {value.length > 8000 ? value.slice(0, 8000) + "\n…" : value}
          </pre>
        ))}
      </div>
    </details>
  );
}

const stopReasons: Record<string, string> = {
  cancelled: "已停止本轮处理。",
  max_tokens: "已达到模型输出上限。",
  max_turn_requests: "已达到单轮请求上限。",
  refusal: "智能体拒绝继续处理。",
  error: "本轮处理失败。",
};

function TranscriptView({
  transcript,
  root,
  openFile,
  respond,
  authenticate,
  authMethods,
}: {
  transcript: Transcript;
  root: string;
  openFile?: (path: string, line?: number) => void;
  respond: (key: string, option: string | null) => void;
  authenticate: (method: string) => void;
  authMethods: AgentSessionSummary["auth_methods"];
}) {
  const terminalTools = new Set(
    transcript.items.flatMap((item) =>
      item.kind === "tool"
        ? item.content.flatMap((content) =>
            content.type === "terminal" ? [content.terminalId] : [],
          )
        : [],
    ),
  );
  return (
    <>
      {transcript.items.map((item) => {
        switch (item.kind) {
          case "user":
            return (
              <div key={item.key} className="agent-user">
                <p>{item.text}</p>
                {item.context.length > 0 && (
                  <div className="agent-chips">
                    {item.context.map((path) => (
                      <button
                        key={path}
                        type="button"
                        className="agent-chip"
                        onClick={() => openFile?.(path)}
                      >
                        <Icon name="file" size={12} />
                        {path.split("/").at(-1)}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          case "agent":
            return item.text.trim() ? (
              <div key={item.key} className="agent-message">
                <MarkdownPreview content={item.text} onOpenFile={(path) => openFile?.(path)} />
              </div>
            ) : null;
          case "thought":
            return item.text.trim() ? (
              <details key={item.key} className="agent-thought">
                <summary>
                  <Icon name="chevron-right" size={12} />
                  {t("思考过程")}
                </summary>
                <p>{item.text}</p>
              </details>
            ) : null;
          case "tool":
            return (
              <ToolCard
                key={item.key}
                item={item}
                root={root}
                terminals={transcript.terminals}
                openFile={openFile}
              />
            );
          case "plan":
            return (
              <div key={item.key} className="agent-plan">
                <div className="agent-section-title">
                  <Icon name="list" size={13} />
                  {t("计划")}
                </div>
                <ol>
                  {item.entries.map((entry, index) => (
                    <li key={index} className={`is-${entry.status ?? "pending"}`}>
                      <span aria-hidden="true" />
                      {entry.content}
                    </li>
                  ))}
                </ol>
              </div>
            );
          case "permission":
            return (
              <div
                key={item.key}
                className={`agent-permission${item.settled ? " is-settled" : ""}`}
              >
                <div className="agent-section-title">
                  <Icon name="lock" size={13} />
                  {t("智能体请求授权")}
                </div>
                {item.title && <p>{item.title}</p>}
                {item.settled ? (
                  <p className="agent-muted">
                    {item.optionId
                      ? t("已选择：{option}", {
                          option:
                            item.options.find((option) => option.optionId === item.optionId)
                              ?.name ?? item.optionId,
                        })
                      : t("请求已取消。")}
                  </p>
                ) : (
                  <div className="agent-permission-actions">
                    {item.options.map((option) => (
                      <button
                        key={option.optionId}
                        type="button"
                        className={
                          option.kind?.startsWith("allow") ? "core-primary" : "agent-secondary"
                        }
                        onClick={() => respond(item.requestKey, option.optionId)}
                      >
                        {option.name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          case "file":
            return (
              <div key={item.key} className="agent-file">
                {item.newText != null ? (
                  <DiffSummary
                    path={item.path}
                    oldText={item.oldText ?? ""}
                    newText={item.newText}
                    created={item.created}
                    openFile={openFile}
                  />
                ) : (
                  <span>
                    <Icon name="edit" size={12} /> {item.path}
                  </span>
                )}
              </div>
            );
          case "turn":
            return item.stopReason !== "end_turn" ? (
              <p
                key={item.key}
                className={`agent-turn${item.stopReason === "error" ? " is-error" : ""}`}
              >
                {t(stopReasons[item.stopReason] ?? "本轮处理结束。")}
                {item.error ? ` ${item.error}` : ""}
              </p>
            ) : null;
          case "notice":
            return (
              <div key={item.key} className={`agent-notice is-${item.tone}`} role="alert">
                <pre>{item.text}</pre>
                {transcript.state === "auth_required" && authMethods.length > 0 && (
                  <div className="agent-permission-actions">
                    {authMethods.map((method) => (
                      <button
                        key={method.id}
                        type="button"
                        className="agent-secondary"
                        title={method.description}
                        onClick={() => authenticate(method.id)}
                      >
                        {method.name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
        }
        return null;
      })}
      {Object.entries(transcript.terminals)
        .filter(([id]) => !terminalTools.has(id))
        .map(([id, terminal]) => (
          <TerminalView key={id} terminal={terminal} />
        ))}
    </>
  );
}

function Picker({
  label,
  value,
  items,
  disabled,
}: {
  label: string;
  value: string;
  items: NavigatorMenuItem[];
  disabled?: boolean;
}) {
  return (
    <NavigatorMenu
      label={label}
      title={`${label}: ${value}`}
      items={items}
      direction="up"
      triggerDisabled={disabled}
      triggerClassName="agent-picker"
      triggerIcon={
        <span className="agent-picker-label">
          <span>{value}</span>
          <Icon name="chevron-down" size={12} />
        </span>
      }
    />
  );
}

function AgentWelcome({
  service,
  selected,
  choose,
  resume,
  error,
}: {
  service?: AgentServiceState;
  selected?: string;
  choose: (id: string) => void;
  resume: (id: string) => void;
  error: string;
}) {
  const recent = (service?.sessions ?? []).filter((session) => liveStates.has(session.state));
  return (
    <div className="agent-welcome">
      <div className="agent-welcome-icon">
        <Icon name="sparkles" size={26} />
      </div>
      <h2>{t("使用智能体处理工程任务")}</h2>
      <p>
        {t(
          "智能体在当前项目中读写源文件并运行命令。修改通过源代码管理审查，工程判定以构建和检查结果为准。",
        )}
      </p>
      {error && <p className="agent-notice is-error">{error}</p>}
      {service && (
        <div className="agent-catalog" role="radiogroup" aria-label={t("选择智能体")}>
          {service.agents.map((agent) => (
            <button
              key={agent.id}
              type="button"
              role="radio"
              aria-checked={agent.id === selected}
              disabled={!agent.available}
              className="agent-catalog-item"
              title={agent.available ? agent.command : agent.reason}
              onClick={() => choose(agent.id)}
            >
              <span
                className={`agent-dot${agent.available ? " is-available" : ""}`}
                aria-hidden="true"
              />
              <span className="agent-catalog-text">
                <span className="agent-catalog-name">
                  {agent.name}
                  {agent.source === "local" && <span className="agent-tag">{t("本机")}</span>}
                </span>
                <span className="agent-catalog-detail">
                  {agent.available ? agent.description || agent.command : t(agent.reason)}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}
      {service && (
        <p className="agent-config-note">
          {t("在 {path} 中配置智能体命令和所需的环境变量，例如 API 密钥。", {
            path: service.config_path,
          })}
        </p>
      )}
      {service?.diagnostics.map((message) => (
        <p key={message} className="agent-notice is-error">
          {message}
        </p>
      ))}
      {recent.length > 0 && (
        <div className="agent-recent">
          <div className="agent-section-title">{t("进行中的会话")}</div>
          {recent.map((session) => (
            <button
              key={session.id}
              type="button"
              className="agent-recent-item"
              onClick={() => resume(session.id)}
            >
              <Icon name="history" size={13} />
              <span>{sessionLabel(session)}</span>
              <small>{session.agent_name}</small>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Agent panel controller: actions for the right panel header and the panel body. */
export function useAgentPanel({
  enabled,
  visible,
  activeFile,
  openFile,
  reviewChanges,
  openProject,
}: AgentPanelProps): { actions: ReactNode; body: ReactNode; prompting: boolean } {
  const [service, setService] = useState<AgentServiceState>();
  const [serviceError, setServiceError] = useState("");
  const [sessionId, setSessionIdState] = useState<string | undefined>(() =>
    readStorage(SESSION_KEY, globalThis.sessionStorage),
  );
  const [summary, setSummary] = useState<AgentSessionSummary>();
  const [transcript, setTranscript] = useState<Transcript>(emptyTranscript);
  const [selectedAgent, setSelectedAgent] = useState<string | undefined>(() =>
    readStorage(AGENT_KEY),
  );
  const [draft, setDraft] = useState("");
  const [includeActive, setIncludeActive] = useState(() => readStorage(CONTEXT_KEY) !== "false");
  const [pending, setPending] = useState<{ text: string; context: string[] }>();
  const [error, setError] = useState("");
  const [commandIndex, setCommandIndex] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const input = useRef<HTMLTextAreaElement>(null);
  const root = service?.root ?? "";

  const setSessionId = useCallback((id: string | undefined) => {
    writeStorage(SESSION_KEY, id, globalThis.sessionStorage);
    setSessionIdState(id);
  }, []);

  const loadService = useCallback(async () => {
    try {
      const next = await request<AgentServiceState>("/api/agent");
      setService(next);
      setServiceError("");
      return next;
    } catch (cause) {
      setServiceError(String(cause instanceof Error ? cause.message : cause));
      return undefined;
    }
  }, []);

  useEffect(() => {
    if (enabled && visible) void loadService();
  }, [enabled, visible, loadService]);

  const agents = service?.agents ?? [];
  const agent: AgentInfo | undefined =
    agents.find((item) => item.id === selectedAgent && item.available) ??
    agents.find((item) => item.id === "deepseek-harness" && item.available) ??
    agents.find((item) => item.available);

  // Long-poll the backend event log of the current session.
  useEffect(() => {
    if (!enabled || !sessionId || !root) return;
    let stopped = false;
    const controller = new AbortController();
    let after = 0;
    let current = emptyTranscript();
    setTranscript(current);
    setSummary(undefined);
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
          setTranscript(current);
          setSummary(batch.session);
          if (batch.session.state === "closed") return;
        } catch {
          if (stopped) return;
          failures += 1;
          const latest = await loadService();
          if (latest && !latest.sessions.some((session) => session.id === sessionId)) {
            setSessionId(undefined);
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
  }, [enabled, sessionId, root, loadService, setSessionId]);

  // Ask the workbench to re-read files and Git after the agent touched the project.
  const announced = useRef(0);
  useEffect(() => {
    if (transcript.fileChanges <= announced.current) {
      announced.current = transcript.fileChanges;
      return;
    }
    announced.current = transcript.fileChanges;
    const timer = setTimeout(
      () => window.dispatchEvent(new Event("texsys:workspace:changed")),
      400,
    );
    return () => clearTimeout(timer);
  }, [transcript.fileChanges]);

  useLayoutEffect(() => {
    const element = scroller.current;
    if (element && stick.current) element.scrollTop = element.scrollHeight;
  }, [transcript, pending]);

  const state = summary?.state ?? (sessionId ? "starting" : undefined);
  const prompting = state === "prompting";

  const sendPrompt = useCallback(async (id: string, text: string, context: string[]) => {
    try {
      await request("/api/agent/prompt", { session: id, text, context });
      setError("");
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
      setDraft((value) => value || text);
    }
  }, []);

  useEffect(() => {
    if (!pending || !sessionId) return;
    if (state === "ready") {
      setPending(undefined);
      void sendPrompt(sessionId, pending.text, pending.context);
    } else if (state && ["error", "exited", "closed"].includes(state)) {
      setDraft((value) => value || pending.text);
      setPending(undefined);
    }
  }, [pending, sessionId, state, sendPrompt]);

  const startSession = useCallback(async () => {
    if (!agent) throw new Error(t("没有可用的智能体。"));
    const created = await request<AgentSessionSummary>("/api/agent/sessions", { agent: agent.id });
    setSessionId(created.id);
    void loadService();
    return created.id;
  }, [agent, loadService, setSessionId]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || prompting || pending) return;
    const context = includeActive && activeFile ? [activeFile] : [];
    setDraft("");
    stick.current = true;
    try {
      if (!sessionId || !state || !liveStates.has(state)) {
        await startSession();
        setPending({ text, context });
      } else if (state === "ready") {
        await sendPrompt(sessionId, text, context);
      } else {
        setPending({ text, context });
      }
      setError("");
    } catch (cause) {
      setDraft(text);
      setError(String(cause instanceof Error ? cause.message : cause));
    }
  }, [
    draft,
    prompting,
    pending,
    includeActive,
    activeFile,
    sessionId,
    state,
    startSession,
    sendPrompt,
  ]);

  const call = useCallback(async (path: string, body: Record<string, unknown>) => {
    try {
      await request(path, body);
      setError("");
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
    }
  }, []);

  const stop = () => sessionId && void call("/api/agent/cancel", { session: sessionId });
  const newSession = () => {
    setSessionId(undefined);
    setSummary(undefined);
    setTranscript(emptyTranscript());
    setPending(undefined);
    setError("");
    void loadService();
    requestAnimationFrame(() => input.current?.focus());
  };
  const closeSession = async (id: string) => {
    await call("/api/agent/close", { session: id });
    if (id === sessionId) newSession();
    else void loadService();
  };

  const commands = transcript.commands;
  const slash = draft.startsWith("/") && !draft.includes(" ") && !draft.includes("\n");
  const matches = slash
    ? commands
        .filter((command) => command.name.toLowerCase().startsWith(draft.slice(1).toLowerCase()))
        .slice(0, 8)
    : [];

  const sessions = service?.sessions ?? [];
  const historyItems: NavigatorMenuItem[] = sessions.length
    ? sessions.map((session) => ({
        id: session.id,
        label: `${sessionLabel(session)} · ${session.agent_name}`,
        checked: session.id === sessionId,
        reason: t(stateLabels[session.state] ?? session.state),
        run: () => {
          stick.current = true;
          setSessionId(session.id);
        },
      }))
    : [{ label: t("暂无会话"), reason: t("发送消息后创建会话。") }];
  const moreItems: NavigatorMenuItem[] = [
    {
      label: t("关闭当前会话"),
      icon: "close",
      run: sessionId ? () => void closeSession(sessionId) : undefined,
      reason: sessionId ? undefined : t("当前没有会话。"),
    },
    { label: t("刷新智能体列表"), icon: "refresh", run: () => void loadService() },
    ...(reviewChanges
      ? [
          {
            label: t("在源代码管理中审查修改"),
            icon: "branch",
            run: reviewChanges,
            separator: true,
          },
        ]
      : []),
  ];

  const actions = enabled ? (
    <>
      <NavigatorButton label={t("新建会话")} icon="plus" onClick={newSession} />
      <NavigatorMenu
        label={t("会话历史")}
        items={historyItems}
        triggerIcon={<Icon name="history" size={16} />}
        onOpen={() => void loadService()}
      />
      <NavigatorMenu label={t("更多智能体操作")} items={moreItems} />
    </>
  ) : null;

  const configPicker = (option: ConfigOption) => {
    const choices = configChoices(option);
    const current = choices.find((choice) => choice.value === option.currentValue);
    return (
      <Picker
        key={option.id}
        label={option.name}
        value={current?.name ?? option.currentValue}
        disabled={prompting}
        items={choices.map((choice) => ({
          id: choice.value,
          label: choice.name,
          checked: choice.value === option.currentValue,
          reason: choice.description,
          run: () =>
            sessionId &&
            void call("/api/agent/config", {
              session: sessionId,
              config: option.id,
              value: choice.value,
            }),
        }))}
      />
    );
  };

  const liveSession = Boolean(sessionId && state && liveStates.has(state));
  const canType = enabled && (liveSession || Boolean(agent));
  const contextName = activeFile?.split("/").at(-1);
  const usage = transcript.usage && transcript.usage.size > 0 ? transcript.usage : undefined;

  const body = !enabled ? (
    <div className="agent-panel">
      <div className="agent-welcome">
        <div className="agent-welcome-icon">
          <Icon name="sparkles" size={26} />
        </div>
        <h2>{t("使用智能体处理工程任务")}</h2>
        <p>{t("打开已启动的项目后，智能体可以在项目中读写源文件并运行构建与检查。")}</p>
        {openProject && (
          <button type="button" className="core-primary" onClick={openProject}>
            {t("打开已启动的项目")}
          </button>
        )}
      </div>
    </div>
  ) : (
    <div className="agent-panel">
      <div
        ref={scroller}
        className="agent-transcript"
        role="log"
        aria-live="polite"
        aria-label={t("智能体对话")}
        onScroll={(event) => {
          const element = event.currentTarget;
          stick.current = element.scrollHeight - element.scrollTop - element.clientHeight < 40;
        }}
      >
        {!sessionId ? (
          <AgentWelcome
            service={service}
            selected={agent?.id}
            error={serviceError}
            choose={(id) => {
              writeStorage(AGENT_KEY, id);
              setSelectedAgent(id);
              input.current?.focus();
            }}
            resume={(id) => setSessionId(id)}
          />
        ) : (
          <>
            <div className="agent-session-head">
              <Icon name="sparkles" size={13} />
              <span>{summary?.agent_name ?? agent?.name}</span>
              {summary?.agent_version && <small>{summary.agent_version}</small>}
              <span className={`agent-state is-${state}`}>
                {t(stateLabels[state ?? "starting"] ?? state ?? "")}
              </span>
            </div>
            <TranscriptView
              transcript={transcript}
              root={root}
              openFile={openFile}
              respond={(key, option) =>
                void call("/api/agent/permission", { session: sessionId, key, option })
              }
              authenticate={(method) =>
                void call("/api/agent/authenticate", { session: sessionId, method })
              }
              authMethods={summary?.auth_methods ?? []}
            />
            {pending && (
              <div className="agent-user is-pending">
                <p>{pending.text}</p>
              </div>
            )}
            {(state === "starting" || prompting || pending) && (
              <div className="agent-working">
                <span className="agent-spinner" aria-hidden="true" />
                {state === "starting" ? t("正在启动智能体…") : t("正在处理…")}
              </div>
            )}
          </>
        )}
      </div>
      {transcript.changed.length > 0 && sessionId && (
        <details className="agent-changes">
          <summary>
            <Icon name="chevron-right" size={12} />
            <span>
              {t("智能体修改了 {count} 个文件", { count: String(transcript.changed.length) })}
            </span>
            {reviewChanges && (
              <button
                type="button"
                className="agent-link-button"
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
                <button
                  type="button"
                  className="agent-link-button"
                  title={path}
                  onClick={() => openFile?.(path)}
                >
                  <Icon name="file" size={12} />
                  {path}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
      {error && (
        <p className="agent-error" role="alert">
          {error}
        </p>
      )}
      <div className="agent-composer">
        {matches.length > 0 && (
          <div className="agent-commands" role="listbox" aria-label={t("智能体命令")}>
            {matches.map((command, index) => (
              <button
                key={command.name}
                type="button"
                role="option"
                aria-selected={index === commandIndex}
                onMouseDown={(event) => {
                  event.preventDefault();
                  setDraft(`/${command.name} `);
                }}
              >
                <code>/{command.name}</code>
                <span>{command.description}</span>
              </button>
            ))}
          </div>
        )}
        {activeFile && (
          <div className="agent-chips">
            <button
              type="button"
              className={`agent-chip${includeActive ? "" : " is-off"}`}
              aria-pressed={includeActive}
              title={
                includeActive
                  ? t("发送时附带当前文件：{path}", { path: activeFile })
                  : t("不附带当前文件")
              }
              onClick={() => {
                writeStorage(CONTEXT_KEY, String(!includeActive));
                setIncludeActive(!includeActive);
              }}
            >
              <Icon name={includeActive ? "file" : "eye-off"} size={12} />
              {contextName}
              <small>{t("当前文件")}</small>
            </button>
          </div>
        )}
        <textarea
          ref={input}
          value={draft}
          rows={3}
          disabled={!canType}
          aria-label={t("向智能体发送消息")}
          placeholder={
            canType
              ? t("描述要完成的工程任务，Enter 发送，Shift+Enter 换行")
              : t("没有可用的智能体，请先完成配置。")
          }
          onChange={(event) => {
            setDraft(event.target.value);
            setCommandIndex(0);
          }}
          onKeyDown={(event) => {
            if (matches.length && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
              event.preventDefault();
              setCommandIndex(
                (index) =>
                  (index + (event.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length,
              );
              return;
            }
            if (matches.length && event.key === "Tab") {
              event.preventDefault();
              setDraft(`/${matches[commandIndex]?.name ?? matches[0].name} `);
              return;
            }
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void send();
            }
            if (event.key === "Escape" && prompting) {
              event.preventDefault();
              stop();
            }
          }}
        />
        <div className="agent-composer-bar">
          {liveSession ? (
            <span className="agent-picker is-static" title={summary?.agent_name}>
              <Icon name="sparkles" size={12} />
              {summary?.agent_name ?? agent?.name}
            </span>
          ) : (
            <Picker
              label={t("智能体")}
              value={agent?.name ?? t("未配置")}
              items={
                agents.length
                  ? agents.map((item) => ({
                      id: item.id,
                      label: item.name,
                      checked: item.id === agent?.id,
                      reason: item.available ? item.command : item.reason,
                      run: item.available
                        ? () => {
                            writeStorage(AGENT_KEY, item.id);
                            setSelectedAgent(item.id);
                          }
                        : undefined,
                    }))
                  : [{ label: t("没有可用的智能体") }]
              }
            />
          )}
          {liveSession &&
            transcript.configOptions.filter((option) => option.type === "select").map(configPicker)}
          {liveSession && transcript.modes && transcript.modes.availableModes.length > 1 && (
            <Picker
              label={t("模式")}
              value={
                transcript.modes.availableModes.find(
                  (mode) => mode.id === transcript.modes?.currentModeId,
                )?.name ?? transcript.modes.currentModeId
              }
              disabled={prompting}
              items={transcript.modes.availableModes.map((mode) => ({
                id: mode.id,
                label: mode.name,
                checked: mode.id === transcript.modes?.currentModeId,
                reason: mode.description,
                run: () =>
                  sessionId && void call("/api/agent/mode", { session: sessionId, mode: mode.id }),
              }))}
            />
          )}
          <span className="agent-spacer" />
          {usage && (
            <span
              className="agent-usage"
              title={t("上下文已用 {used} / {size} tokens", {
                used: usage.used.toLocaleString(),
                size: usage.size.toLocaleString(),
              })}
            >
              {Math.max(1, Math.round((usage.used / usage.size) * 100))}%
            </span>
          )}
          {prompting ? (
            <button
              type="button"
              className="agent-send is-stop"
              aria-label={t("停止")}
              title={t("停止 (Esc)")}
              onClick={stop}
            >
              <Icon name="stop" size={14} />
            </button>
          ) : (
            <button
              type="button"
              className="agent-send"
              aria-label={t("发送")}
              title={t("发送 (Enter)")}
              disabled={!draft.trim() || !canType || Boolean(pending)}
              onClick={() => void send()}
            >
              <Icon name="send" size={14} />
            </button>
          )}
        </div>
      </div>
    </div>
  );

  return { actions, body, prompting };
}

const stateLabels: Record<string, string> = {
  starting: "启动中",
  ready: "就绪",
  prompting: "处理中",
  auth_required: "需要登录",
  error: "启动失败",
  exited: "已退出",
  closed: "已关闭",
};

/** Right panel with VS Code style view tabs: properties and the agent. */
export function RightPanel({
  view,
  changeView,
  properties,
  agent,
}: {
  view: "properties" | "agent";
  changeView: (view: "properties" | "agent") => void;
  properties: ReactNode;
  agent: AgentPanelProps;
}) {
  const controller = useAgentPanel({ ...agent, visible: agent.visible && view === "agent" });
  const tabs = [
    { id: "properties" as const, label: t("属性"), icon: "info" },
    { id: "agent" as const, label: t("智能体"), icon: "sparkles" },
  ];
  return (
    <div className="right-panel">
      <header className="right-panel-heading">
        <div className="right-panel-tabs" role="tablist" aria-label={t("右侧面板视图")}>
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`right-panel-tab-${tab.id}`}
              aria-selected={view === tab.id}
              aria-controls={`right-panel-view-${tab.id}`}
              tabIndex={view === tab.id ? 0 : -1}
              onClick={() => changeView(tab.id)}
              onKeyDown={(event) => {
                if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                event.preventDefault();
                const next = tab.id === "agent" ? "properties" : "agent";
                changeView(next);
                document.getElementById(`right-panel-tab-${next}`)?.focus();
              }}
            >
              {tab.label}
              {tab.id === "agent" && controller.prompting && (
                <span className="right-panel-busy" aria-label={t("处理中")} />
              )}
            </button>
          ))}
        </div>
        {view === "agent" && <div className="right-panel-actions">{controller.actions}</div>}
      </header>
      <div
        id="right-panel-view-properties"
        role="tabpanel"
        aria-labelledby="right-panel-tab-properties"
        className="right-panel-view"
        hidden={view !== "properties"}
      >
        {properties}
      </div>
      <div
        id="right-panel-view-agent"
        role="tabpanel"
        aria-labelledby="right-panel-tab-agent"
        className="right-panel-view"
        hidden={view !== "agent"}
      >
        {controller.body}
      </div>
    </div>
  );
}
