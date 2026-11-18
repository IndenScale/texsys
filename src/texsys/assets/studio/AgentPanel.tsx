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
import { NavigatorButton, NavigatorPanel } from "./NavigatorPanel";
import { NavigatorMenu, type NavigatorMenuItem } from "./NavigatorMenu";
import { MarkdownPreview } from "./MarkdownPreview";
import { t, localizeError, useLocale } from "./i18n";
import {
  applyAgentEvents,
  cleanModelName,
  configChoices,
  contextWindowConfigOption,
  diffStats,
  emptyTranscript,
  formatContextTokens,
  formatThinkingLabel,
  inferModelContextTokens,
  lineDiff,
  mergeModelGroups,
  modelCatalog,
  presentTool,
  reasoningConfigOption,
  sessionLabel,
  type AgentEventBatch,
  type AgentInfo,
  type AgentServiceState,
  type AgentSessionSummary,
  type ConfigOption,
  type ModelGroup,
  type NormalizedModelChoice,
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
const VIEW_KEY = "texsys.agent.viewPrefs";
const MODEL_CACHE_KEY = "texsys.agent.modelLabel";
const MODEL_GROUPS_CACHE_KEY = "texsys.agent.modelGroups";
const liveStates = new Set(["starting", "ready", "prompting", "auth_required"]);

type PermissionLevel = "default" | "autoApprove";
type ActivePopover = "context" | "model" | "harness" | "permission" | "usage" | null;
type ModelPopoverView = "controls" | "models";
type SessionFilter = "all" | "active" | "ended";

type ViewPrefs = {
  showSessionBar: boolean;
  compactTools: boolean;
};

const defaultViewPrefs: ViewPrefs = {
  showSessionBar: true,
  compactTools: false,
};

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

function readViewPrefs(): ViewPrefs {
  try {
    const raw = readStorage(VIEW_KEY);
    if (!raw) return defaultViewPrefs;
    const parsed = JSON.parse(raw) as Partial<ViewPrefs>;
    return {
      showSessionBar:
        typeof parsed.showSessionBar === "boolean"
          ? parsed.showSessionBar
          : defaultViewPrefs.showSessionBar,
      compactTools:
        typeof parsed.compactTools === "boolean"
          ? parsed.compactTools
          : defaultViewPrefs.compactTools,
    };
  } catch {
    return defaultViewPrefs;
  }
}

function readModelCache(): Record<string, string> {
  try {
    const raw = readStorage(MODEL_CACHE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, string>;
    return Object.fromEntries(
      Object.entries(parsed).map(([k, v]) => [k, cleanModelName(String(v)).name]),
    );
  } catch {
    return {};
  }
}

function readModelGroupsCache(): Record<string, ModelGroup[]> {
  try {
    const raw = readStorage(MODEL_GROUPS_CACHE_KEY);
    return raw ? (JSON.parse(raw) as Record<string, ModelGroup[]>) : {};
  } catch {
    return {};
  }
}

function harnessIcon(id: string): string {
  if (id.includes("claude")) return "sparkles";
  if (id.includes("codex")) return "code";
  if (id.includes("gemini")) return "sparkles";
  if (id.includes("deepseek")) return "terminal";
  return "bot";
}

export type AgentPanelProps = {
  /** Workspace scope: bound project or standalone empty workspace. */
  workspace?: "project" | "empty";
  /** The right panel is open; refreshes the agent list when shown. */
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

function FloatingPopover({
  anchorRef,
  open,
  onClose,
  onEscape,
  className = "",
  label,
  children,
}: {
  anchorRef: React.RefObject<HTMLElement | null>;
  open: boolean;
  onClose: () => void;
  onEscape?: () => boolean;
  className?: string;
  label: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const pop = ref.current;
    const anchor = anchorRef.current;
    if (!open || !pop || !anchor) return;
    const place = () => {
      const rect = anchor.getBoundingClientRect();
      const edge = 8;
      const gap = 6;
      pop.style.maxHeight = "";
      const spaceAbove = rect.top - gap - edge;
      const spaceBelow = window.innerHeight - rect.bottom - gap - edge;
      const measured = pop.getBoundingClientRect();
      if (measured.width === 0 && measured.height === 0) return;
      const preferAbove = spaceAbove >= Math.min(measured.height, 180) || spaceAbove >= spaceBelow;
      const maxAvail = Math.max(150, preferAbove ? spaceAbove : spaceBelow);
      if (measured.height > maxAvail) {
        pop.style.maxHeight = `${Math.min(340, maxAvail)}px`;
      }
      const { width, height } = pop.getBoundingClientRect();
      const maxX = Math.max(edge, window.innerWidth - width - edge);
      const x = Math.max(edge, Math.min(rect.left, maxX));
      const y = preferAbove
        ? Math.max(edge, rect.top - gap - height)
        : Math.min(rect.bottom + gap, Math.max(edge, window.innerHeight - height - edge));
      pop.style.left = `${x}px`;
      pop.style.top = `${y}px`;
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open, anchorRef, children]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: Event) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (ref.current?.contains(target) || anchorRef.current?.contains(target)) return;
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        if (onEscape?.()) return;
        onClose();
        anchorRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [open, onClose, onEscape, anchorRef]);

  if (!open) return null;
  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={label}
      translate="no"
      className={`agent-popover notranslate ${className}`.trim()}
    >
      {children}
    </div>
  );
}

type SliderChoice = {
  value: string;
  label: string;
  description?: string;
};

/** ChatGPT / Codex style discrete pill-track slider with drag preview and snap-on-release. */
function DiscretePillSlider({
  label,
  choices,
  currentValue,
  fallbackLabel,
  disabled = false,
  onCommit,
}: {
  label: string;
  choices: SliderChoice[];
  currentValue?: string;
  fallbackLabel: string;
  disabled?: boolean;
  onCommit: (value: string) => void;
}) {
  const sliderRef = useRef<HTMLDivElement>(null);
  const committedIndex = useMemo(() => {
    if (choices.length === 0) return 0;
    const found = choices.findIndex((item) => item.value === currentValue);
    return found >= 0 ? found : 0;
  }, [choices, currentValue]);

  const [drag, setDrag] = useState<{
    pointerId: number;
    fraction: number;
    index: number;
  } | null>(null);
  const dragRef = useRef<{
    pointerId: number;
    fraction: number;
    index: number;
  } | null>(null);

  const available = !disabled && choices.length >= 2;
  const maxIndex = Math.max(0, choices.length - 1);
  const activeIndex = drag ? drag.index : committedIndex;
  const fraction = !available
    ? choices.length === 1
      ? 1
      : 0
    : drag
      ? drag.fraction
      : maxIndex > 0
        ? committedIndex / maxIndex
        : 0;
  const displayValue = choices[activeIndex]?.label ?? fallbackLabel;

  const computePoint = (clientX: number) => {
    const el = sliderRef.current;
    const rect = el?.getBoundingClientRect();
    const width = rect && rect.width > 24 ? rect.width : 200;
    const left = rect ? rect.left : 0;
    const nextFraction = Math.max(0, Math.min(1, (clientX - left - 12) / Math.max(1, width - 24)));
    const nextIndex = Math.round(nextFraction * maxIndex);
    return { fraction: nextFraction, index: nextIndex };
  };

  const cancelDrag = useCallback((pointerId?: number) => {
    const el = sliderRef.current;
    if (pointerId !== undefined && el?.hasPointerCapture?.(pointerId)) {
      el.releasePointerCapture(pointerId);
    }
    dragRef.current = null;
    setDrag(null);
  }, []);

  useEffect(() => {
    if (!drag) return;
    const onEscapeCapture = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        cancelDrag(drag.pointerId);
      }
    };
    document.addEventListener("keydown", onEscapeCapture, true);
    return () => document.removeEventListener("keydown", onEscapeCapture, true);
  }, [drag, cancelDrag]);

  return (
    <div className={`agent-slider-group${available ? "" : " is-unavailable"}`}>
      <div className="agent-slider-head">
        <span className="agent-slider-label">{label}</span>
        <span className="agent-slider-value">{displayValue}</span>
      </div>
      <div
        ref={sliderRef}
        role="slider"
        tabIndex={available ? 0 : -1}
        className={`agent-slider-control${available ? "" : " is-unavailable"}${
          drag ? " is-dragging" : ""
        }`}
        aria-label={label}
        aria-orientation="horizontal"
        aria-disabled={!available}
        aria-valuemin={0}
        aria-valuemax={maxIndex}
        aria-valuenow={activeIndex}
        aria-valuetext={displayValue}
        style={{ "--slider-fraction": String(fraction) } as React.CSSProperties}
        onPointerDown={(event) => {
          if (!available || !event.isPrimary || event.button !== 0) return;
          event.preventDefault();
          sliderRef.current?.focus({ preventScroll: true });
          sliderRef.current?.setPointerCapture?.(event.pointerId);
          const next = { pointerId: event.pointerId, ...computePoint(event.clientX) };
          dragRef.current = next;
          setDrag(next);
        }}
        onPointerMove={(event) => {
          if (!dragRef.current || dragRef.current.pointerId !== event.pointerId) return;
          const next = { pointerId: event.pointerId, ...computePoint(event.clientX) };
          dragRef.current = next;
          setDrag(next);
        }}
        onPointerUp={(event) => {
          if (!dragRef.current || dragRef.current.pointerId !== event.pointerId) return;
          const pt = computePoint(event.clientX);
          cancelDrag(event.pointerId);
          if (pt.index !== committedIndex && choices[pt.index]) {
            onCommit(choices[pt.index].value);
          }
        }}
        onPointerCancel={(event) => {
          if (dragRef.current && dragRef.current.pointerId === event.pointerId) {
            cancelDrag(event.pointerId);
          }
        }}
        onLostPointerCapture={(event) => {
          if (dragRef.current && dragRef.current.pointerId === event.pointerId) {
            dragRef.current = null;
            setDrag(null);
          }
        }}
        onKeyDown={(event) => {
          if (!available) return;
          const stepMap: Record<string, number> = {
            ArrowRight: activeIndex + 1,
            ArrowUp: activeIndex + 1,
            ArrowLeft: activeIndex - 1,
            ArrowDown: activeIndex - 1,
            Home: 0,
            End: maxIndex,
          };
          const target = stepMap[event.key];
          if (target === undefined) return;
          event.preventDefault();
          event.stopPropagation();
          if (dragRef.current) cancelDrag(dragRef.current.pointerId);
          const clamped = Math.max(0, Math.min(maxIndex, target));
          if (clamped !== committedIndex && choices[clamped]) {
            onCommit(choices[clamped].value);
          }
        }}
      >
        <div className="agent-slider-track" aria-hidden="true">
          <span className="agent-slider-fill" />
        </div>
        {choices.length > 1 && (
          <div className="agent-slider-stops" aria-hidden="true">
            {choices.map((choice) => (
              <i key={choice.value} />
            ))}
          </div>
        )}
        <span className="agent-slider-thumb" aria-hidden="true" />
      </div>
    </div>
  );
}

function ContextUsageRing({
  used,
  size,
  active,
  buttonRef,
  onClick,
}: {
  used: number;
  size: number;
  active: boolean;
  buttonRef: React.RefObject<HTMLButtonElement | null>;
  onClick: () => void;
}) {
  const ratio = Math.min(1, Math.max(0, used / size));
  const pct = Math.max(1, Math.round(ratio * 100));
  const r = 5;
  const circumference = 2 * Math.PI * r;
  const dashOffset = circumference * (1 - Math.max(0.06, ratio));
  const tone = pct >= 90 ? " is-error" : pct >= 75 ? " is-warning" : "";
  return (
    <button
      ref={buttonRef}
      type="button"
      className={`agent-usage${tone}${active ? " is-active" : ""}`}
      aria-expanded={active}
      aria-label={t("上下文用量")}
      title={t("上下文已用 {used} / {size} tokens", {
        used: used.toLocaleString(),
        size: size.toLocaleString(),
      })}
      onClick={onClick}
    >
      <span className="agent-usage-ring" aria-hidden="true">
        <svg viewBox="0 0 14 14" width={14} height={14}>
          <circle className="agent-usage-bg" cx="7" cy="7" r={r} />
          <circle
            className="agent-usage-arc"
            cx="7"
            cy="7"
            r={r}
            strokeDasharray={circumference}
            strokeDashoffset={dashOffset}
          />
        </svg>
      </span>
      <span className="agent-usage-label">{pct}%</span>
    </button>
  );
}

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
  compact,
  openFile,
}: {
  item: Extract<TranscriptItem, { kind: "tool" }>;
  root: string;
  terminals: Record<string, TerminalRecord>;
  compact?: boolean;
  openFile?: (path: string, line?: number) => void;
}) {
  const view = presentTool(item, root);
  const showCommandBlock = Boolean(view.command && view.terminals.length === 0);
  const hasBody =
    view.diffs.length > 0 || view.text.length > 0 || view.terminals.length > 0 || showCommandBlock;
  const head = (
    <>
      <span className={`agent-tool-status is-${item.status}`}>
        <StatusIcon status={item.status} />
      </span>
      <Icon name={view.icon} size={13} />
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
      {hasBody && (
        <span className="agent-tool-chevron" aria-hidden="true">
          <Icon name="chevron-right" size={12} />
        </span>
      )}
    </>
  );
  const cls = `agent-tool is-${item.status}${compact ? " is-compact" : ""}`;
  if (!hasBody) return <div className={cls}>{head}</div>;
  return (
    <details className={cls} open={item.status === "failed"}>
      <summary>{head}</summary>
      <div className="agent-tool-body">
        {showCommandBlock && (
          <div className="agent-terminal">
            <div className="agent-terminal-head">
              <Icon name="terminal" size={12} />
              <code className="is-wrap">{view.command}</code>
            </div>
            {view.text.map((value, index) => (
              <pre key={index}>{value.length > 8000 ? value.slice(0, 8000) + "\n…" : value}</pre>
            ))}
          </div>
        )}
        {view.diffs.map((diff, index) => (
          <DiffSummary key={index} {...diff} openFile={openFile} />
        ))}
        {view.terminals.map((id) => (
          <TerminalView key={id} terminal={terminals[id]} />
        ))}
        {!showCommandBlock &&
          view.text.map((value, index) => (
            <pre key={index} className="agent-tool-output">
              {value.length > 8000 ? value.slice(0, 8000) + "\n…" : value}
            </pre>
          ))}
      </div>
    </details>
  );
}

function CopyResponseButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={`agent-response-action${copied ? " is-copied" : ""}`}
      aria-label={t(copied ? "已复制" : "复制回答")}
      title={t(copied ? "已复制" : "复制回答")}
      onClick={() => {
        void navigator.clipboard?.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1400);
      }}
    >
      <Icon name={copied ? "tick" : "copy"} size={13} />
    </button>
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
  compactTools,
  attribution,
  prompting,
  openFile,
  respond,
  authenticate,
  authMethods,
}: {
  transcript: Transcript;
  root: string;
  compactTools: boolean;
  attribution?: string;
  prompting: boolean;
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
  const lastAgentIndex = transcript.items.findLastIndex(
    (item) => item.kind === "agent" && Boolean(item.text.trim()),
  );
  return (
    <>
      {transcript.items.map((item, idx) => {
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
                        <span>{path.split("/").at(-1)}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          case "agent": {
            if (!item.text.trim()) return null;
            const isLatest = idx === lastAgentIndex && !prompting;
            return (
              <div key={item.key} className={`agent-message${isLatest ? " is-most-recent" : ""}`}>
                <MarkdownPreview content={item.text} onOpenFile={(path) => openFile?.(path)} />
                <div className="agent-response-footer">
                  <CopyResponseButton text={item.text} />
                  {attribution && <span className="agent-response-meta">{attribution}</span>}
                </div>
              </div>
            );
          }
          case "thought": {
            if (!item.text.trim()) return null;
            const streaming = prompting && idx === transcript.items.length - 1;
            return (
              <details
                key={item.key}
                className={`agent-thought${streaming ? " is-streaming" : ""}`}
              >
                <summary>
                  <Icon name="chevron-right" size={12} />
                  <span>{t("思考过程")}</span>
                </summary>
                <div className="agent-thought-body">{item.text}</div>
              </details>
            );
          }
          case "tool":
            return (
              <ToolCard
                key={item.key}
                item={item}
                root={root}
                terminals={transcript.terminals}
                compact={compactTools}
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
                  <Icon name="shield" size={13} />
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

function HarnessSetupCard({
  service,
  selected,
  choose,
  onRefresh,
  onClose,
}: {
  service?: AgentServiceState;
  selected?: string;
  choose: (id: string) => void;
  onRefresh: () => void;
  onClose?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const snippet = `{
  "agents": {
    "deepseek-harness": {
      "env": { "DEEPSEEK_API_KEY": "..." }
    }
  }
}`;
  return (
    <div className="agent-setup-card" role="region" aria-label={t("智能体配置…")}>
      <div className="agent-setup-head">
        <Icon name="monitor" size={14} />
        <span>{t("选择智能体运行环境")}</span>
        <button
          type="button"
          className="agent-icon-btn"
          aria-label={t("刷新智能体列表")}
          title={t("刷新智能体列表")}
          onClick={onRefresh}
        >
          <Icon name="refresh" size={13} />
        </button>
        {onClose && (
          <button
            type="button"
            className="agent-icon-btn"
            aria-label={t("隐藏")}
            title={t("隐藏")}
            onClick={onClose}
          >
            <Icon name="close" size={13} />
          </button>
        )}
      </div>
      {service && (
        <div className="agent-catalog" role="radiogroup" aria-label={t("选择智能体")}>
          {service.agents.map((item) => (
            <button
              key={item.id}
              type="button"
              role="radio"
              aria-checked={item.id === selected}
              disabled={!item.available}
              className="agent-catalog-item"
              title={item.available ? item.command : item.reason}
              onClick={() => choose(item.id)}
            >
              <span
                className={`agent-dot${item.available ? " is-available" : ""}`}
                aria-hidden="true"
              />
              <span className="agent-catalog-text">
                <span className="agent-catalog-name">
                  {item.name}
                  {item.source === "local" && <span className="agent-tag">{t("本机")}</span>}
                  {!item.available && <span className="agent-tag">{t("未安装")}</span>}
                </span>
                <span className="agent-catalog-detail">
                  {item.available ? item.description || item.command : t(item.reason)}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}
      {service && (
        <div className="agent-setup-note">
          <p>
            {t("在 {path} 中配置智能体命令和所需的环境变量，例如 API 密钥。", {
              path: service.config_path,
            })}
          </p>
          <div className="agent-setup-code">
            <pre>{snippet}</pre>
            <button
              type="button"
              className="agent-secondary"
              onClick={() => {
                void navigator.clipboard?.writeText(snippet);
                setCopied(true);
                setTimeout(() => setCopied(false), 1400);
              }}
            >
              <Icon name={copied ? "tick" : "copy"} size={12} />
              {t(copied ? "已复制" : "复制配置示例")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function AgentWelcome({
  workspace,
  service,
  selected,
  showSetup,
  choose,
  onRefresh,
  onCloseSetup,
  error,
}: {
  workspace: "project" | "empty";
  service?: AgentServiceState;
  selected?: string;
  showSetup: boolean;
  choose: (id: string) => void;
  onRefresh: () => void;
  onCloseSetup: () => void;
  error: string;
}) {
  const hasAvailable = Boolean(service?.agents.some((item) => item.available));
  const needsSetup = Boolean(service && (!hasAvailable || service.diagnostics.length > 0));
  return (
    <div className="agent-welcome">
      <div className="agent-welcome-hero">
        <div className="agent-welcome-icon">
          <Icon name="sparkles" size={22} />
        </div>
        <h2>{t(workspace === "empty" ? "使用智能体提问或探索方案" : "使用智能体处理工程任务")}</h2>
        <p>
          {t(
            workspace === "empty"
              ? "当前未打开项目工作区，可直接提问讨论；打开项目后智能体可读写源文件并运行构建与检查。"
              : "智能体在当前项目中读写源文件并运行命令。修改通过源代码管理审查，工程判定以构建和检查结果为准。",
          )}
        </p>
      </div>
      {error && <p className="agent-notice is-error">{error}</p>}
      {service?.diagnostics.map((message) => (
        <p key={message} className="agent-notice is-error">
          {message}
        </p>
      ))}
      {(showSetup || needsSetup) && (
        <HarnessSetupCard
          service={service}
          selected={selected}
          choose={choose}
          onRefresh={onRefresh}
          onClose={needsSetup ? undefined : onCloseSetup}
        />
      )}
    </div>
  );
}

function AgentDebugDrawer({
  service,
  summary,
  transcript,
  onClose,
}: {
  service?: AgentServiceState;
  summary?: AgentSessionSummary;
  transcript: Transcript;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const payload = useMemo(() => {
    const lines = [
      `workspace: ${service?.root ?? "-"}`,
      `config: ${service?.config_path ?? "-"}`,
      `session: ${summary?.id ?? "(none)"} · state=${summary?.state ?? transcript.state}`,
      ...(summary?.command ? [`command: ${summary.command}`] : []),
      ...(summary?.cwd ? [`cwd: ${summary.cwd}`] : []),
      ...(summary?.agent_version ? [`version: ${summary.agent_version}`] : []),
      ...(Object.keys(summary?.capabilities ?? {}).length
        ? [`capabilities: ${JSON.stringify(summary?.capabilities)}`]
        : []),
      ...(summary?.stderr ? [`stderr:\n${summary.stderr}`] : []),
      ...transcript.debugLog.map((entry) => `#${entry.seq} [${entry.kind}] ${entry.detail}`),
    ];
    return lines.join("\n");
  }, [service, summary, transcript.state, transcript.debugLog]);

  return (
    <section className="agent-debug" aria-label={t("智能体调试日志")}>
      <header className="agent-debug-head">
        <Icon name="terminal" size={12} />
        <span>{t("智能体调试日志")}</span>
        <button
          type="button"
          className="agent-icon-btn"
          aria-label={t(copied ? "已复制" : "复制调试日志")}
          title={t(copied ? "已复制" : "复制调试日志")}
          onClick={() => {
            void navigator.clipboard?.writeText(payload);
            setCopied(true);
            setTimeout(() => setCopied(false), 1400);
          }}
        >
          <Icon name={copied ? "tick" : "copy"} size={12} />
        </button>
        <button
          type="button"
          className="agent-icon-btn"
          aria-label={t("关闭调试日志")}
          title={t("关闭调试日志")}
          onClick={onClose}
        >
          <Icon name="close" size={12} />
        </button>
      </header>
      <pre>{payload}</pre>
    </section>
  );
}

/** Agent panel controller: VS Code Chat ViewPane layout, split session bar, and two-tier input. */
export function useAgentPanel({
  workspace = "project",
  visible,
  maximized = false,
  activeFile,
  openFile,
  reviewChanges,
  openProject,
  onToggleMaximize,
  onClose,
}: AgentPanelProps): {
  actions: ReactNode;
  body: ReactNode;
  prompting: boolean;
  onHeaderContextMenu: (event: React.MouseEvent<HTMLElement>) => void;
} {
  const locale = useLocale();
  const mac = /Mac|iPhone|iPad/.test(globalThis.navigator?.platform ?? "");
  const sessionKey = `${SESSION_KEY}.${workspace}`;
  const [service, setService] = useState<AgentServiceState>();
  const [serviceError, setServiceError] = useState("");
  const [sessionId, setSessionIdState] = useState<string | undefined>(() =>
    readStorage(sessionKey, globalThis.sessionStorage),
  );
  const [summary, setSummary] = useState<AgentSessionSummary>();
  const [transcript, setTranscript] = useState<Transcript>(emptyTranscript);
  const [selectedAgent, setSelectedAgent] = useState<string | undefined>(() =>
    readStorage(AGENT_KEY),
  );
  const [draft, setDraft] = useState("");
  const [includeActive, setIncludeActive] = useState(() => readStorage(CONTEXT_KEY) !== "false");
  const [extraContext, setExtraContext] = useState<string[]>([]);
  const [projectFiles, setProjectFiles] = useState<string[]>([]);
  const [contextFilter, setContextFilter] = useState("");
  const [pending, setPending] = useState<{ text: string; context: string[] }>();
  const [error, setError] = useState("");
  const [commandIndex, setCommandIndex] = useState(0);
  const [viewPrefs, setViewPrefsState] = useState<ViewPrefs>(readViewPrefs);
  const [sessionsExpanded, setSessionsExpanded] = useState(false);
  const [sessionSearchOpen, setSessionSearchOpen] = useState(false);
  const [sessionQuery, setSessionQuery] = useState("");
  const [sessionFilter, setSessionFilter] = useState<SessionFilter>("all");
  const [permissionLevel, setPermissionLevel] = useState<PermissionLevel>("default");
  const [showSetup, setShowSetup] = useState(false);
  const [showDebugLog, setShowDebugLog] = useState(false);
  const [activePopover, setActivePopover] = useState<ActivePopover>(null);
  const [modelPopoverView, setModelPopoverView] = useState<ModelPopoverView>("controls");
  const [modelFilter, setModelFilter] = useState("");
  const [modelCache, setModelCache] = useState<Record<string, string>>(readModelCache);
  const [modelGroupsCache, setModelGroupsCache] =
    useState<Record<string, ModelGroup[]>>(readModelGroupsCache);
  const [atBottom, setAtBottom] = useState(true);
  const [headerContextPoint, setHeaderContextPoint] = useState<{ x: number; y: number }>();

  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const input = useRef<HTMLTextAreaElement>(null);
  const contextBtnRef = useRef<HTMLButtonElement>(null);
  const modelBtnRef = useRef<HTMLButtonElement>(null);
  const modelListRef = useRef<HTMLDivElement>(null);
  const harnessBtnRef = useRef<HTMLButtonElement>(null);
  const permissionBtnRef = useRef<HTMLButtonElement>(null);
  const usageBtnRef = useRef<HTMLButtonElement>(null);
  const pendingModelChoiceRef = useRef<{ agentId: string; value: string }>();
  const autoApprovedKeys = useRef(new Set<string>());
  const root = service?.root ?? "";

  const updateViewPrefs = useCallback((patch: Partial<ViewPrefs>) => {
    setViewPrefsState((prev) => {
      const next = { ...prev, ...patch };
      writeStorage(VIEW_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const setSessionId = useCallback(
    (id: string | undefined) => {
      writeStorage(sessionKey, id, globalThis.sessionStorage);
      setSessionIdState(id);
    },
    [sessionKey],
  );

  useEffect(() => {
    setSessionIdState(readStorage(sessionKey, globalThis.sessionStorage));
  }, [sessionKey]);

  const loadService = useCallback(async () => {
    try {
      const next = await request<AgentServiceState>(
        `/api/agent?workspace=${encodeURIComponent(workspace)}`,
      );
      setService(next);
      setServiceError("");
      return next;
    } catch (cause) {
      setServiceError(String(cause instanceof Error ? cause.message : cause));
      return undefined;
    }
  }, [workspace]);

  useEffect(() => {
    if (visible) void loadService();
  }, [visible, loadService]);

  const agents = service?.agents ?? [];
  const agent: AgentInfo | undefined =
    agents.find((item) => item.id === selectedAgent && item.available) ??
    agents.find((item) => item.id === "deepseek-harness" && item.available) ??
    agents.find((item) => item.available);

  // Auto-size composer textarea smoothly without browser resize handles.
  useLayoutEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    const nextHeight = Math.min(180, Math.max(40, el.scrollHeight));
    el.style.height = `${nextHeight}px`;
  }, [draft, visible]);

  // Long-poll the backend event log of the current session.
  useEffect(() => {
    if (!sessionId || !root) return;
    let stopped = false;
    const controller = new AbortController();
    let after = 0;
    let current = emptyTranscript();
    setTranscript(current);
    setSummary(undefined);
    autoApprovedKeys.current.clear();
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
  }, [sessionId, root, loadService, setSessionId]);

  // Cache latest clean model name and provider groups per harness.
  const activeAgentId = summary?.agent ?? agent?.id;
  const activeAgentName = summary?.agent_name ?? agent?.name ?? t("未配置");
  const models = useMemo(
    () => modelCatalog(transcript, activeAgentName, activeAgentId),
    [transcript, activeAgentName, activeAgentId],
  );
  const reasoningOption = useMemo(() => reasoningConfigOption(transcript), [transcript]);
  const contextOption = useMemo(() => contextWindowConfigOption(transcript), [transcript]);

  useEffect(() => {
    if (!activeAgentId || !models?.currentLabel) return;
    setModelCache((prev) => {
      if (prev[activeAgentId] === models.currentLabel) return prev;
      const next = { ...prev, [activeAgentId]: models.currentLabel };
      writeStorage(MODEL_CACHE_KEY, JSON.stringify(next));
      return next;
    });
  }, [activeAgentId, models?.currentLabel]);

  useEffect(() => {
    if (!activeAgentId || !models?.groups.length) return;
    setModelGroupsCache((prev) => {
      const serializedPrev = JSON.stringify(prev[activeAgentId]);
      const serializedNext = JSON.stringify(models.groups);
      if (serializedPrev === serializedNext) return prev;
      const next = { ...prev, [activeAgentId]: models.groups };
      writeStorage(MODEL_GROUPS_CACHE_KEY, JSON.stringify(next));
      return next;
    });
  }, [activeAgentId, models?.groups]);

  // Ask the workbench to re-read files and Git after the agent touched the project.
  const announced = useRef(0);
  useEffect(() => {
    if (workspace !== "project") return;
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
  }, [workspace, transcript.fileChanges]);

  useLayoutEffect(() => {
    const element = scroller.current;
    if (element && stick.current) {
      element.scrollTop = element.scrollHeight;
      setAtBottom(true);
    }
  }, [transcript, pending]);

  const state = summary?.state ?? (sessionId ? "starting" : undefined);
  const prompting = state === "prompting";

  const call = useCallback(async (path: string, body: Record<string, unknown>) => {
    try {
      await request(path, body);
      setError("");
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
    }
  }, []);

  // Auto-approve permission requests when user elevated session permission level to "autoApprove".
  useEffect(() => {
    if (permissionLevel !== "autoApprove" || !sessionId) return;
    for (const item of transcript.items) {
      if (item.kind !== "permission" || item.settled) continue;
      if (autoApprovedKeys.current.has(item.requestKey)) continue;
      const allow =
        item.options.find((opt) => opt.kind === "allow_once") ??
        item.options.find((opt) => opt.kind?.startsWith("allow"));
      if (allow) {
        autoApprovedKeys.current.add(item.requestKey);
        void call("/api/agent/permission", {
          session: sessionId,
          key: item.requestKey,
          option: allow.optionId,
        });
      }
    }
  }, [permissionLevel, sessionId, transcript.items, call]);

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

  const startSession = useCallback(
    async (targetAgentId?: string) => {
      const chosen =
        (targetAgentId
          ? agents.find((item) => item.id === targetAgentId && item.available)
          : undefined) ?? agent;
      if (!chosen) throw new Error(t("没有可用的智能体。"));
      const created = await request<AgentSessionSummary>("/api/agent/sessions", {
        agent: chosen.id,
        workspace,
      });
      setSessionId(created.id);
      void loadService();
      return created.id;
    },
    [agents, agent, workspace, loadService, setSessionId],
  );

  const attachedFiles = useMemo(() => {
    if (workspace !== "project") return [];
    const list: string[] = [];
    if (includeActive && activeFile) list.push(activeFile);
    for (const file of extraContext) {
      if (!list.includes(file) && list.length < 16) list.push(file);
    }
    return list;
  }, [workspace, includeActive, activeFile, extraContext]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || prompting || pending) return;
    const context = attachedFiles;
    setDraft("");
    setExtraContext([]);
    stick.current = true;
    setAtBottom(true);
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
  }, [draft, prompting, pending, attachedFiles, sessionId, state, startSession, sendPrompt]);

  const stop = () => sessionId && void call("/api/agent/cancel", { session: sessionId });
  const newSession = useCallback(
    (targetAgentId?: string) => {
      if (targetAgentId) {
        writeStorage(AGENT_KEY, targetAgentId);
        setSelectedAgent(targetAgentId);
      }
      setSessionId(undefined);
      setSummary(undefined);
      setTranscript(emptyTranscript());
      setPending(undefined);
      setError("");
      setActivePopover(null);
      void loadService();
      requestAnimationFrame(() => input.current?.focus());
    },
    [loadService, setSessionId],
  );

  const chooseHarness = useCallback(
    (targetId: string) => {
      writeStorage(AGENT_KEY, targetId);
      setSelectedAgent(targetId);
      setActivePopover(null);
      setShowSetup(false);
      if (sessionId && summary?.agent && summary.agent !== targetId) {
        const existing = (service?.sessions ?? []).find(
          (item) => item.agent === targetId && liveStates.has(item.state),
        );
        if (existing) {
          stick.current = true;
          setSessionId(existing.id);
        } else {
          newSession(targetId);
        }
      } else {
        requestAnimationFrame(() => input.current?.focus());
      }
    },
    [sessionId, summary?.agent, service?.sessions, setSessionId, newSession],
  );

  const closeSession = async (id: string) => {
    await call("/api/agent/close", { session: id });
    if (id === sessionId) newSession();
    else void loadService();
  };

  // Load compact project file index when opening the "+" context picker.
  const openContextPopover = useCallback(() => {
    setActivePopover((current) => (current === "context" ? null : "context"));
    setContextFilter("");
    if (workspace === "project") {
      void request<{ files?: (string | { path: string })[] }>("/api/files?compact=1")
        .then((res) => {
          const items = (res.files ?? [])
            .map((entry) => (typeof entry === "string" ? entry : entry.path))
            .filter(Boolean);
          setProjectFiles(items);
        })
        .catch(() => {});
    }
  }, [workspace]);

  const ensureSessionForConfig = useCallback(() => {
    if (!sessionId && agent?.available && !pending) {
      void startSession().catch((cause) => {
        setError(String(cause instanceof Error ? cause.message : cause));
      });
    }
  }, [sessionId, agent?.available, pending, startSession]);

  const openModelPopover = useCallback(
    (initialView: ModelPopoverView = "controls") => {
      setModelFilter("");
      setModelPopoverView(initialView);
      ensureSessionForConfig();
      setActivePopover("model");
    },
    [ensureSessionForConfig],
  );

  const toggleModelPopover = useCallback(() => {
    if (activePopover === "model") {
      setActivePopover(null);
      return;
    }
    openModelPopover("controls");
  }, [activePopover, openModelPopover]);

  const commands = transcript.commands;
  const slash = draft.startsWith("/") && !draft.includes(" ") && !draft.includes("\n");
  const matches = slash
    ? commands
        .filter((command) => command.name.toLowerCase().startsWith(draft.slice(1).toLowerCase()))
        .slice(0, 8)
    : [];

  const hasConversation = Boolean(
    sessionId && (summary?.title || pending || transcript.items.length > 0),
  );
  const sessions = useMemo(
    () =>
      (service?.sessions ?? []).filter(
        (item) => Boolean(item.title) || item.id !== sessionId || hasConversation,
      ),
    [service?.sessions, sessionId, hasConversation],
  );
  const filteredSessions = useMemo(() => {
    const q = sessionQuery.trim().toLowerCase();
    return sessions.filter((item) => {
      if (sessionFilter === "active" && !liveStates.has(item.state)) return false;
      if (sessionFilter === "ended" && liveStates.has(item.state)) return false;
      if (!q) return true;
      return (
        sessionLabel(item).toLowerCase().includes(q) || item.agent_name.toLowerCase().includes(q)
      );
    });
  }, [sessions, sessionFilter, sessionQuery]);

  // Split "+ v" New Chat menu items (matching VS Code chatNewActions.ts).
  const newChatMenuItems: NavigatorMenuItem[] = [
    {
      label: t("新建会话"),
      icon: "plus",
      shortcut: [mac ? "⌘" : "Ctrl", "N"],
      run: () => newSession(),
    },
    ...(agents.length > 0
      ? agents.map((item, idx) => ({
          id: `new-${item.id}`,
          label: t("新建 {name} 会话", { name: item.name }),
          icon: harnessIcon(item.id),
          separator: idx === 0,
          reason: item.available ? item.command : item.reason,
          run: item.available ? () => newSession(item.id) : undefined,
        }))
      : []),
  ];

  const isDefaultView =
    viewPrefs.showSessionBar === defaultViewPrefs.showSessionBar &&
    viewPrefs.compactTools === defaultViewPrefs.compactTools &&
    !showDebugLog &&
    !maximized;

  // Overflow "..." menu items (matching VS Code ViewTitle menu).
  const moreItems: NavigatorMenuItem[] = [
    {
      label: t("新建会话"),
      icon: "plus",
      run: () => newSession(),
    },
    {
      label: t("关闭当前会话"),
      icon: "close",
      run: sessionId ? () => void closeSession(sessionId) : undefined,
      reason: sessionId ? undefined : t("当前没有会话。"),
    },
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
    ...(workspace === "empty" && openProject
      ? [
          {
            label: t("打开已启动的项目"),
            icon: "grid",
            run: openProject,
            separator: true,
          },
        ]
      : []),
    {
      label: t("视图模式"),
      separator: true,
      submenu: [
        {
          label: t("显示会话栏"),
          checkbox: true,
          checked: viewPrefs.showSessionBar,
          run: () => updateViewPrefs({ showSessionBar: !viewPrefs.showSessionBar }),
        },
        {
          label: t("紧凑工具卡片"),
          checkbox: true,
          checked: viewPrefs.compactTools,
          run: () => updateViewPrefs({ compactTools: !viewPrefs.compactTools }),
        },
      ],
    },
    {
      label: t("智能体配置…"),
      icon: "settings",
      run: () => {
        setShowSetup((value) => !value);
        if (sessionId) setSessionId(undefined);
      },
    },
    { label: t("刷新智能体列表"), icon: "refresh", run: () => void loadService() },
    {
      label: t("显示智能体调试日志"),
      checkbox: true,
      checked: showDebugLog,
      separator: true,
      run: () => setShowDebugLog((value) => !value),
    },
    {
      label: t("默认显示视图"),
      checked: isDefaultView,
      separator: true,
      run: () => {
        updateViewPrefs(defaultViewPrefs);
        setShowDebugLog(false);
        setSessionsExpanded(false);
        if (maximized) onToggleMaximize?.();
      },
    },
  ];

  const filterMenuItems: NavigatorMenuItem[] = [
    {
      label: t("全部会话"),
      checked: sessionFilter === "all",
      run: () => setSessionFilter("all"),
    },
    {
      label: t("进行中"),
      checked: sessionFilter === "active",
      run: () => setSessionFilter("active"),
    },
    {
      label: t("已结束"),
      checked: sessionFilter === "ended",
      run: () => setSessionFilter("ended"),
    },
  ];

  const actions = (
    <>
      {prompting && <span className="agent-busy-dot" aria-label={t("处理中")} />}
      <div className="agent-split-button">
        <button
          type="button"
          className="agent-split-main navigator-button"
          aria-label={t("新建会话")}
          title={`${t("新建会话")} (${mac ? "⌘N" : "Ctrl+N"})`}
          onClick={() => newSession()}
        >
          <Icon name="plus" size={16} />
        </button>
        <NavigatorMenu
          label={t("新建会话选项")}
          title={t("新建会话选项")}
          items={newChatMenuItems}
          placement="start"
          direction="down"
          triggerClassName="agent-split-chevron"
          triggerIcon={<Icon name="chevron-down" size={12} />}
          onOpen={() => void loadService()}
        />
      </div>
      <NavigatorMenu
        label={t("更多智能体操作")}
        title={t("更多操作…")}
        items={moreItems}
        placement="end"
        direction="down"
      />
      {onToggleMaximize && (
        <NavigatorButton
          className="agent-window-action-start"
          label={t(maximized ? "还原面板大小" : "最大化面板大小")}
          icon={maximized ? "panel-restore" : "panel-maximize"}
          onClick={onToggleMaximize}
        />
      )}
      {onClose && (
        <NavigatorButton
          className={onToggleMaximize ? "" : "agent-window-action-start"}
          label={t("关闭智能体面板")}
          icon="close"
          onClick={onClose}
        />
      )}
    </>
  );

  const liveSession = Boolean(sessionId && state && liveStates.has(state));
  const canType = liveSession || Boolean(agent);
  const contextName = workspace === "project" ? activeFile?.split("/").at(-1) : undefined;
  const usage = transcript.usage && transcript.usage.size > 0 ? transcript.usage : undefined;
  const currentModelLabel =
    models?.currentLabel ?? (activeAgentId ? modelCache[activeAgentId] : undefined);

  const reasoningChoices = useMemo(
    () =>
      reasoningOption
        ? configChoices(reasoningOption).map((choice) => ({
            value: choice.value,
            label: formatThinkingLabel(choice.name || choice.value, locale),
            description: choice.description,
          }))
        : [],
    [reasoningOption, locale],
  );

  const contextChoices = useMemo(
    () =>
      contextOption
        ? configChoices(contextOption).map((choice) => ({
            value: choice.value,
            label: formatContextTokens(choice.name || choice.value) || choice.name || choice.value,
            description: choice.description,
          }))
        : [],
    [contextOption],
  );

  const currentEffortLabel = useMemo(() => {
    if (!reasoningOption) return "";
    const active = reasoningChoices.find((c) => c.value === reasoningOption.currentValue);
    return active?.label ?? formatThinkingLabel(reasoningOption.currentValue, locale);
  }, [reasoningOption, reasoningChoices, locale]);

  const inferredContextLabel = useMemo(
    () => inferModelContextTokens(currentModelLabel, usage?.size),
    [currentModelLabel, usage?.size],
  );

  const currentContextLabel = useMemo(() => {
    if (contextOption) {
      const active = contextChoices.find((c) => c.value === contextOption.currentValue);
      return active?.label ?? formatContextTokens(contextOption.currentValue);
    }
    return inferredContextLabel ?? "";
  }, [contextOption, contextChoices, inferredContextLabel]);

  const currentModeName = transcript.modes?.availableModes.find(
    (mode) => mode.id === transcript.modes?.currentModeId,
  )?.name;
  const permissionLabel =
    permissionLevel === "autoApprove"
      ? t("自动允许")
      : currentModeName
        ? `${t("默认权限")} · ${currentModeName}`
        : t("默认权限");

  const showSessionsList =
    sessionsExpanded || (!hasConversation && sessions.length > 0 && viewPrefs.showSessionBar);
  const hasStackTop = Boolean(transcript.changed.length > 0 && sessionId);

  const selectModel = useCallback(
    (value: string) => {
      if (!sessionId || !models) return;
      if (models.source === "config" && models.configId) {
        void call("/api/agent/config", {
          session: sessionId,
          config: models.configId,
          value,
        });
      } else {
        void call("/api/agent/model", { session: sessionId, model: value });
      }
      setActivePopover(null);
    },
    [sessionId, models, call],
  );

  // Apply cross-harness model selection once the target harness finishes connecting.
  useEffect(() => {
    const queued = pendingModelChoiceRef.current;
    if (!queued || !sessionId || !models || activeAgentId !== queued.agentId || state !== "ready")
      return;
    pendingModelChoiceRef.current = undefined;
    selectModel(queued.value);
  }, [sessionId, models, activeAgentId, state, selectModel]);

  const selectModelChoice = useCallback(
    (choice: NormalizedModelChoice) => {
      if (choice.agentId && choice.agentId !== activeAgentId) {
        pendingModelChoiceRef.current = { agentId: choice.agentId, value: choice.value };
        setActivePopover(null);
        writeStorage(AGENT_KEY, choice.agentId);
        setSelectedAgent(choice.agentId);
        setSessionId(undefined);
        setSummary(undefined);
        setTranscript(emptyTranscript());
        void startSession(choice.agentId).catch((cause) => {
          pendingModelChoiceRef.current = undefined;
          setError(String(cause instanceof Error ? cause.message : cause));
        });
        return;
      }
      selectModel(choice.value);
    },
    [activeAgentId, selectModel, setSessionId, startSession],
  );

  const selectConfigOption = useCallback(
    (option: ConfigOption, value: string) => {
      if (!sessionId) return;
      void call("/api/agent/config", {
        session: sessionId,
        config: option.id,
        value,
      });
    },
    [sessionId, call],
  );

  const stackedModelGroups = useMemo(() => {
    const currentGroups =
      models?.groups ?? (activeAgentId ? modelGroupsCache[activeAgentId] : undefined);
    const otherGroups = agents
      .filter((item) => item.available && item.id !== activeAgentId)
      .map((item) => modelGroupsCache[item.id]);
    return mergeModelGroups(currentGroups, ...otherGroups);
  }, [models?.groups, activeAgentId, modelGroupsCache, agents]);

  const filteredModelGroups = useMemo(() => {
    const q = modelFilter.trim().toLowerCase();
    if (!q) return stackedModelGroups;
    return stackedModelGroups
      .map((group) => {
        if (group.label.toLowerCase().includes(q)) return group;
        return {
          ...group,
          options: group.options.filter((choice) => choice.name.toLowerCase().includes(q)),
        };
      })
      .filter((group) => group.options.length > 0);
  }, [stackedModelGroups, modelFilter]);

  const filteredProjectFiles = useMemo(() => {
    const q = contextFilter.trim().toLowerCase();
    const list = projectFiles.filter((file) => file !== activeFile);
    return (q ? list.filter((file) => file.toLowerCase().includes(q)) : list).slice(0, 30);
  }, [projectFiles, activeFile, contextFilter]);

  const onHeaderContextMenu = useCallback((event: React.MouseEvent<HTMLElement>) => {
    if ((event.target as HTMLElement | null)?.closest("button")) return;
    event.preventDefault();
    setHeaderContextPoint({ x: event.clientX, y: event.clientY });
  }, []);

  const body = (
    <div
      className="agent-panel"
      onKeyDown={(event) => {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "n") {
          event.preventDefault();
          newSession();
        }
        if (
          (event.metaKey || event.ctrlKey) &&
          event.shiftKey &&
          event.key.toLowerCase() === "m" &&
          canType
        ) {
          event.preventDefault();
          openModelPopover("models");
        }
      }}
    >
      {headerContextPoint && (
        <NavigatorMenu
          label={t("更多智能体操作")}
          items={moreItems}
          point={headerContextPoint}
          onDismiss={() => setHeaderContextPoint(undefined)}
        />
      )}

      {viewPrefs.showSessionBar && (
        <div className="agent-sessions-shell" translate="no">
          {hasConversation && !sessionsExpanded ? (
            <div className="agent-title-control">
              <button
                type="button"
                className="agent-title-back"
                title={t("返回新建会话")}
                onClick={() => newSession()}
              >
                <Icon name="back" size={16} />
                <span className="agent-title-harness">{activeAgentName}</span>
                {summary?.title && summary.title !== activeAgentName && (
                  <>
                    <span className="agent-title-sep" aria-hidden="true">
                      ·
                    </span>
                    <span className="agent-title-label">{summary.title}</span>
                  </>
                )}
              </button>
              <div className="agent-subbar-actions">
                {state && ["auth_required", "error", "exited"].includes(state) && (
                  <span className={`agent-state-pill is-${state}`}>
                    {t(stateLabels[state] ?? state)}
                  </span>
                )}
                <button
                  type="button"
                  className="agent-icon-btn"
                  aria-label={t("切换会话列表")}
                  title={t("切换会话列表")}
                  onClick={() => {
                    setSessionsExpanded(true);
                    void loadService();
                  }}
                >
                  <Icon name="sessions-pane" size={16} />
                </button>
              </div>
            </div>
          ) : (
            <div className="agent-subbar">
              <button
                type="button"
                className="agent-subbar-title"
                aria-expanded={showSessionsList}
                onClick={() => setSessionsExpanded((value) => !value)}
              >
                <span>{t("会话")}</span>
                {sessions.length > 0 && (
                  <span className="agent-count-badge">{sessions.length}</span>
                )}
              </button>
              <div className="agent-subbar-actions">
                <button
                  type="button"
                  className="agent-icon-btn"
                  aria-label={t("刷新会话")}
                  title={t("刷新会话")}
                  onClick={() => void loadService()}
                >
                  <Icon name="refresh" size={16} />
                </button>
                <button
                  type="button"
                  className={`agent-icon-btn${sessionSearchOpen ? " is-active" : ""}`}
                  aria-label={t("搜索会话")}
                  title={t("搜索会话")}
                  aria-pressed={sessionSearchOpen}
                  onClick={() => {
                    setSessionSearchOpen((value) => !value);
                    if (!sessionsExpanded) setSessionsExpanded(true);
                  }}
                >
                  <Icon name="search" size={16} />
                </button>
                <NavigatorMenu
                  label={t("筛选会话")}
                  title={t("筛选会话")}
                  items={filterMenuItems}
                  placement="end"
                  direction="down"
                  triggerClassName={`agent-filter-btn${sessionFilter !== "all" ? " is-active" : ""}`}
                  triggerIcon={<Icon name="filter" size={16} />}
                />
                <button
                  type="button"
                  className={`agent-icon-btn${showSessionsList ? " is-active" : ""}`}
                  aria-label={t("切换会话列表")}
                  title={t("切换会话列表")}
                  aria-pressed={showSessionsList}
                  onClick={() => setSessionsExpanded((value) => !value)}
                >
                  <Icon name="sessions-pane" size={16} />
                </button>
              </div>
            </div>
          )}

          {showSessionsList && (
            <div className="agent-sessions-drawer">
              {sessionSearchOpen && (
                <div className="agent-inline-search">
                  <Icon name="search" size={12} />
                  <input
                    type="search"
                    value={sessionQuery}
                    placeholder={t("搜索会话…")}
                    aria-label={t("搜索会话")}
                    onChange={(event) => setSessionQuery(event.target.value)}
                  />
                </div>
              )}
              {filteredSessions.length === 0 ? (
                <p className="agent-sessions-empty">{t("暂无会话")}</p>
              ) : (
                <div className="agent-sessions-list" role="listbox" aria-label={t("会话历史")}>
                  {filteredSessions.map((item) => {
                    const selected = item.id === sessionId;
                    const busy = item.state === "starting" || item.state === "prompting";
                    return (
                      <div
                        key={item.id}
                        className={`agent-session-row${selected ? " is-selected" : ""}`}
                      >
                        <button
                          type="button"
                          role="option"
                          aria-selected={selected}
                          className="agent-session-select"
                          onClick={() => {
                            stick.current = true;
                            setSessionId(item.id);
                            setSessionsExpanded(false);
                          }}
                        >
                          <span className="agent-session-status" aria-hidden="true">
                            {busy ? (
                              <span className="agent-spinner" />
                            ) : (
                              <Icon
                                name={
                                  item.state === "error" || item.state === "exited"
                                    ? "error-circle"
                                    : "sparkles"
                                }
                                size={13}
                              />
                            )}
                          </span>
                          <span className="agent-session-main">
                            <span className="agent-session-title">{sessionLabel(item)}</span>
                            <span className="agent-session-meta">
                              <Icon name={harnessIcon(item.agent)} size={11} />
                              <span>{item.agent_name}</span>
                              <span>·</span>
                              <span>{t(stateLabels[item.state] ?? item.state)}</span>
                            </span>
                          </span>
                        </button>
                        <button
                          type="button"
                          className="agent-session-close"
                          aria-label={t("终止会话")}
                          title={t("终止会话")}
                          onClick={() => void closeSession(item.id)}
                        >
                          <Icon name="close" size={12} />
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="agent-stage">
        <div
          ref={scroller}
          className="agent-transcript"
          role="log"
          aria-live="polite"
          aria-label={t("智能体对话")}
          onScroll={(event) => {
            const element = event.currentTarget;
            const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 40;
            stick.current = nearBottom;
            setAtBottom(nearBottom);
          }}
        >
          {!hasConversation ? (
            <AgentWelcome
              workspace={workspace}
              service={service}
              selected={agent?.id}
              showSetup={showSetup}
              error={serviceError}
              choose={chooseHarness}
              onRefresh={() => void loadService()}
              onCloseSetup={() => setShowSetup(false)}
            />
          ) : (
            <>
              <TranscriptView
                transcript={transcript}
                root={root}
                compactTools={viewPrefs.compactTools}
                attribution={[currentModelLabel, activeAgentName].filter(Boolean).join(" · ")}
                prompting={Boolean(prompting || pending)}
                openFile={openFile}
                respond={(key, option) =>
                  sessionId &&
                  void call("/api/agent/permission", { session: sessionId, key, option })
                }
                authenticate={(method) =>
                  sessionId && void call("/api/agent/authenticate", { session: sessionId, method })
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
                  <span>{state === "starting" ? t("正在启动智能体…") : t("正在处理…")}</span>
                </div>
              )}
            </>
          )}
        </div>

        {!atBottom && hasConversation && (
          <button
            type="button"
            className="agent-scroll-bottom"
            aria-label={t("滚动到底部")}
            title={t("滚动到底部")}
            onClick={() => {
              const el = scroller.current;
              if (el) {
                stick.current = true;
                el.scrollTop = el.scrollHeight;
                setAtBottom(true);
              }
            }}
          >
            <Icon name="arrow-down" size={13} />
          </button>
        )}
      </div>

      {showDebugLog && (
        <AgentDebugDrawer
          service={service}
          summary={summary}
          transcript={transcript}
          onClose={() => setShowDebugLog(false)}
        />
      )}

      {error && (
        <p className="agent-error" role="alert">
          {error}
        </p>
      )}

      <div className="agent-input-part">
        {hasStackTop && (
          <details className="agent-changes">
            <summary>
              <Icon name="chevron-right" size={12} />
              <span className="agent-changes-title">
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
                  {openFile ? (
                    <button
                      type="button"
                      className="agent-link-button"
                      title={path}
                      onClick={() => openFile(path)}
                    >
                      <Icon name="file" size={12} />
                      <span>{path}</span>
                    </button>
                  ) : (
                    <span className="agent-changed-path" title={path}>
                      <Icon name="file" size={12} />
                      <span>{path}</span>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </details>
        )}

        <div
          className={`agent-composer${hasStackTop ? " has-stack-top" : ""}${
            prompting || (state === "starting" && hasConversation) ? " is-working" : ""
          }`}
        >
          <span className="agent-working-border" aria-hidden="true" />
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

          {workspace === "project" && (activeFile || extraContext.length > 0) && (
            <div className="agent-chips">
              {activeFile && (
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
                  <Icon name={includeActive ? "file" : "eye-off"} size={11} />
                  <span>{contextName}</span>
                  <small>{t("当前文件")}</small>
                </button>
              )}
              {extraContext.map((path) => (
                <button
                  key={path}
                  type="button"
                  className="agent-chip"
                  title={path}
                  onClick={() => setExtraContext((prev) => prev.filter((entry) => entry !== path))}
                >
                  <Icon name="file" size={11} />
                  <span>{path.split("/").at(-1)}</span>
                  <Icon name="close" size={10} />
                </button>
              ))}
            </div>
          )}

          <textarea
            ref={input}
            value={draft}
            rows={1}
            disabled={!canType}
            aria-label={t("向智能体发送消息")}
            placeholder={
              canType
                ? t(workspace === "empty" ? "输入问题或要讨论的内容" : "描述要构建的内容")
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

          <div className="agent-composer-bar" translate="no">
            <div className="agent-input-toolbar">
              <button
                ref={contextBtnRef}
                type="button"
                className={`agent-toolbar-chip is-icon-only${
                  activePopover === "context" ? " is-active" : ""
                }`}
                aria-label={t("添加上下文")}
                aria-expanded={activePopover === "context"}
                title={t("添加上下文")}
                onClick={openContextPopover}
              >
                <Icon name="plus" size={14} />
              </button>

              <button
                ref={modelBtnRef}
                type="button"
                disabled={!canType}
                className={`agent-model-trigger notranslate${
                  activePopover === "model" ? " is-active" : ""
                }`}
                aria-label={t("选择模型")}
                aria-haspopup="dialog"
                aria-expanded={activePopover === "model"}
                title={
                  currentModelLabel
                    ? [
                        `${t("选择模型")}: ${currentModelLabel}`,
                        currentEffortLabel,
                        currentContextLabel,
                      ]
                        .filter(Boolean)
                        .join(" · ")
                    : t("选择模型")
                }
                onClick={toggleModelPopover}
              >
                <span className="agent-model-name">{currentModelLabel || t("选择模型")}</span>
                {currentEffortLabel && (
                  <span className="agent-model-meta">{currentEffortLabel}</span>
                )}
                {currentContextLabel && (
                  <span className="agent-model-meta">
                    {currentEffortLabel ? `· ${currentContextLabel}` : currentContextLabel}
                  </span>
                )}
                <span className="agent-model-chevron" aria-hidden="true">
                  <Icon name="chevron-down" size={12} />
                </span>
              </button>
            </div>

            <div className="agent-execute-toolbar">
              {usage && (
                <ContextUsageRing
                  used={usage.used}
                  size={usage.size}
                  active={activePopover === "usage"}
                  buttonRef={usageBtnRef}
                  onClick={() =>
                    setActivePopover((current) => (current === "usage" ? null : "usage"))
                  }
                />
              )}
              {prompting ? (
                <button
                  type="button"
                  className="agent-send is-stop"
                  aria-label={t("停止")}
                  title={t("停止 (Esc)")}
                  onClick={stop}
                >
                  <Icon name="stop" size={11} />
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
                  <Icon name="arrow-up" size={13} strokeWidth={1.8} />
                </button>
              )}
            </div>
          </div>
        </div>

        <div className="agent-secondary-toolbar" translate="no">
          <button
            ref={harnessBtnRef}
            type="button"
            className={`agent-secondary-chip notranslate${
              activePopover === "harness" ? " is-active" : ""
            }`}
            aria-label={t("选择智能体")}
            aria-expanded={activePopover === "harness"}
            title={`${t("选择智能体运行环境")}: ${activeAgentName}`}
            onClick={() => {
              void loadService();
              setActivePopover((current) => (current === "harness" ? null : "harness"));
            }}
          >
            <Icon name="monitor" size={14} />
            <span className="agent-chip-text">{activeAgentName}</span>
          </button>

          <button
            ref={permissionBtnRef}
            type="button"
            className={`agent-secondary-chip notranslate${
              permissionLevel === "autoApprove" ? " is-warning" : ""
            }${activePopover === "permission" ? " is-active" : ""}`}
            aria-label={t("权限与模式")}
            aria-expanded={activePopover === "permission"}
            title={permissionLabel}
            onClick={() =>
              setActivePopover((current) => (current === "permission" ? null : "permission"))
            }
          >
            <Icon name={permissionLevel === "autoApprove" ? "warning" : "shield"} size={14} />
            <span className="agent-chip-text">{permissionLabel}</span>
          </button>
        </div>

        {/* "+" Context Picker Popover */}
        <FloatingPopover
          anchorRef={contextBtnRef}
          open={activePopover === "context"}
          onClose={() => setActivePopover(null)}
          label={t("添加上下文")}
          className="agent-popover-context"
        >
          {workspace !== "project" ? (
            <div className="agent-popover-empty">
              <p>{t("打开项目后可附带工程文件作为上下文")}</p>
              {openProject && (
                <button
                  type="button"
                  className="agent-secondary"
                  onClick={() => {
                    setActivePopover(null);
                    openProject();
                  }}
                >
                  {t("打开已启动的项目")}
                </button>
              )}
            </div>
          ) : (
            <>
              <div className="agent-popover-search">
                <Icon name="search" size={13} />
                <input
                  type="search"
                  autoFocus
                  value={contextFilter}
                  placeholder={t("搜索项目文件…")}
                  aria-label={t("搜索项目文件…")}
                  onChange={(event) => setContextFilter(event.target.value)}
                />
              </div>
              <div className="agent-popover-list" role="listbox" aria-label={t("添加上下文")}>
                {activeFile && (
                  <button
                    type="button"
                    role="option"
                    aria-selected={includeActive}
                    className="agent-popover-row"
                    onClick={() => {
                      writeStorage(CONTEXT_KEY, String(!includeActive));
                      setIncludeActive(!includeActive);
                    }}
                  >
                    <span className="agent-popover-check">
                      {includeActive && <Icon name="tick" size={14} />}
                    </span>
                    <Icon name="file" size={13} />
                    <span className="agent-popover-main">{activeFile}</span>
                    <span className="agent-popover-sub">{t("当前文件")}</span>
                  </button>
                )}
                {filteredProjectFiles.length === 0 && !activeFile ? (
                  <p className="agent-popover-hint">{t("没有匹配的项目文件")}</p>
                ) : (
                  filteredProjectFiles.map((file) => {
                    const checked = extraContext.includes(file);
                    return (
                      <button
                        key={file}
                        type="button"
                        role="option"
                        aria-selected={checked}
                        className="agent-popover-row"
                        onClick={() =>
                          setExtraContext((prev) =>
                            checked ? prev.filter((item) => item !== file) : [...prev, file],
                          )
                        }
                      >
                        <span className="agent-popover-check">
                          {checked && <Icon name="tick" size={14} />}
                        </span>
                        <Icon name="file" size={13} />
                        <span className="agent-popover-main">{file.split("/").at(-1)}</span>
                        <span className="agent-popover-sub">{file}</span>
                      </button>
                    );
                  })
                )}
              </div>
            </>
          )}
        </FloatingPopover>

        {/* ChatGPT-Style Model Controls (Two Sliders) & Stacked Provider Model Picker */}
        <FloatingPopover
          anchorRef={modelBtnRef}
          open={activePopover === "model"}
          onClose={() => setActivePopover(null)}
          onEscape={() => {
            if (modelPopoverView === "models") {
              setModelPopoverView("controls");
              return true;
            }
            return false;
          }}
          label={t("选择模型")}
          className={`agent-popover-model is-${modelPopoverView}`}
        >
          {modelPopoverView === "controls" ? (
            <div className="agent-model-controls">
              <div className="agent-model-controls-head">
                <button
                  type="button"
                  className="agent-model-switch-btn"
                  aria-label={t("选择模型")}
                  onClick={() => {
                    setModelFilter("");
                    setModelPopoverView("models");
                  }}
                >
                  <span className="agent-model-switch-name">
                    {currentModelLabel ||
                      (state === "starting" && !models ? t("正在读取模型…") : t("选择模型"))}
                  </span>
                  <Icon name="chevron-right" size={13} />
                </button>
              </div>

              <DiscretePillSlider
                label={t("推理强度")}
                choices={reasoningChoices}
                currentValue={reasoningOption?.currentValue}
                fallbackLabel={state === "starting" && !models ? `${t("读取中")}…` : t("默认")}
                disabled={prompting || reasoningChoices.length < 2}
                onCommit={(val) => reasoningOption && selectConfigOption(reasoningOption, val)}
              />

              <DiscretePillSlider
                label={t("上下文窗口")}
                choices={contextChoices}
                currentValue={contextOption?.currentValue}
                fallbackLabel={
                  currentContextLabel ||
                  (state === "starting" && !models ? `${t("读取中")}…` : t("默认"))
                }
                disabled={prompting || contextChoices.length < 2}
                onCommit={(val) => contextOption && selectConfigOption(contextOption, val)}
              />
            </div>
          ) : (
            <div
              className="agent-model-catalog-view"
              onKeyDown={(event) => {
                if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
                const options = [
                  ...(modelListRef.current?.querySelectorAll<HTMLButtonElement>(
                    "[role='option']:not(:disabled)",
                  ) ?? []),
                ];
                if (options.length === 0) return;
                event.preventDefault();
                const currentIdx = options.indexOf(document.activeElement as HTMLButtonElement);
                const nextIdx =
                  currentIdx < 0
                    ? event.key === "ArrowDown"
                      ? 0
                      : options.length - 1
                    : (currentIdx + (event.key === "ArrowDown" ? 1 : options.length - 1)) %
                      options.length;
                options[nextIdx]?.focus({ preventScroll: false });
              }}
            >
              <div className="agent-model-search">
                <input
                  type="search"
                  autoFocus
                  value={modelFilter}
                  placeholder={t("搜索模型…")}
                  aria-label={t("搜索模型…")}
                  onChange={(event) => setModelFilter(event.target.value)}
                />
              </div>

              <div
                ref={modelListRef}
                className="agent-model-list"
                role="listbox"
                aria-label={t("选择模型")}
              >
                {state === "starting" && stackedModelGroups.length === 0 ? (
                  <div className="agent-popover-loading">
                    <span className="agent-spinner" aria-hidden="true" />
                    <span>{t("正在读取模型…")}</span>
                  </div>
                ) : stackedModelGroups.length === 0 ? (
                  <p className="agent-popover-hint">{t("没有匹配的模型")}</p>
                ) : filteredModelGroups.length === 0 ? (
                  <p className="agent-popover-hint">{t("没有匹配的模型")}</p>
                ) : (
                  filteredModelGroups.map((group) => (
                    <div
                      key={group.key}
                      className="agent-model-group"
                      role="group"
                      aria-label={group.label}
                    >
                      <div className="agent-model-group-heading">{group.label}</div>
                      {group.options.map((choice) => {
                        const checked =
                          choice.value === models?.currentValue &&
                          (!choice.agentId || choice.agentId === activeAgentId);
                        return (
                          <button
                            key={`${choice.agentId ?? ""}:${choice.value}`}
                            type="button"
                            role="option"
                            aria-selected={checked}
                            disabled={prompting}
                            className={`agent-model-option${checked ? " is-checked" : ""}`}
                            title={choice.name}
                            onClick={() => selectModelChoice(choice)}
                          >
                            <span className="agent-model-option-name">{choice.name}</span>
                            <span className="agent-model-option-check" aria-hidden="true">
                              {checked && <Icon name="tick" size={14} />}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </FloatingPopover>

        {/* Harness Target Picker Popover (benchmarking VS Code SessionTypePickerActionItem / Image 4) */}
        <FloatingPopover
          anchorRef={harnessBtnRef}
          open={activePopover === "harness"}
          onClose={() => setActivePopover(null)}
          label={t("选择智能体")}
          className="agent-popover-harness"
        >
          <div className="agent-popover-list" role="menu" aria-label={t("选择智能体")}>
            {agents.map((item) => {
              const checked = item.id === (summary?.agent ?? agent?.id);
              return (
                <button
                  key={item.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={checked}
                  disabled={!item.available}
                  className={`agent-popover-row${checked ? " is-checked" : ""}`}
                  title={item.available ? item.command : item.reason}
                  onClick={() => chooseHarness(item.id)}
                >
                  <Icon name={harnessIcon(item.id)} size={14} />
                  <span className="agent-popover-main">{item.name}</span>
                  {item.source === "local" && (
                    <span className="agent-popover-badge">{t("本机")}</span>
                  )}
                  {!item.available && (
                    <span className="agent-popover-badge is-muted">{t("未安装")}</span>
                  )}
                  {checked && <Icon name="tick" size={14} />}
                </button>
              );
            })}
          </div>
          <div className="agent-popover-footer">
            <button
              type="button"
              className="agent-popover-footer-action"
              onClick={() => {
                setActivePopover(null);
                setShowSetup(true);
                if (sessionId) setSessionId(undefined);
              }}
            >
              {t("了解与配置智能体 Harness…")}
            </button>
          </div>
        </FloatingPopover>

        {/* Permission & ACP Mode Picker Popover (benchmarking VS Code PermissionPickerActionItem) */}
        <FloatingPopover
          anchorRef={permissionBtnRef}
          open={activePopover === "permission"}
          onClose={() => setActivePopover(null)}
          label={t("权限与模式")}
          className="agent-popover-permission"
        >
          <div className="agent-popover-list" role="menu" aria-label={t("权限与模式")}>
            <button
              type="button"
              role="menuitemradio"
              aria-checked={permissionLevel === "default"}
              className={`agent-permission-item${permissionLevel === "default" ? " is-checked" : ""}`}
              onClick={() => {
                setPermissionLevel("default");
                setActivePopover(null);
              }}
            >
              <span className="agent-popover-check">
                {permissionLevel === "default" ? (
                  <Icon name="tick" size={14} />
                ) : (
                  <Icon name="shield" size={14} />
                )}
              </span>
              <span className="agent-permission-text">
                <span>{t("默认权限")}</span>
                <small>{t("工具请求授权时询问，严格限制在当前工作区")}</small>
              </span>
            </button>

            <button
              type="button"
              role="menuitemradio"
              aria-checked={permissionLevel === "autoApprove"}
              className={`agent-permission-item${
                permissionLevel === "autoApprove" ? " is-checked" : ""
              }`}
              onClick={() => {
                setPermissionLevel("autoApprove");
                setActivePopover(null);
              }}
            >
              <span className="agent-popover-check">
                {permissionLevel === "autoApprove" ? (
                  <Icon name="tick" size={14} />
                ) : (
                  <Icon name="warning" size={14} />
                )}
              </span>
              <span className="agent-permission-text">
                <span>{t("自动允许")}</span>
                <small>{t("本会话自动批准工具授权请求，不中断多步执行")}</small>
              </span>
            </button>

            {transcript.modes && transcript.modes.availableModes.length > 0 && (
              <div className="agent-options-section">
                <div className="agent-popover-heading">{t("模式")}</div>
                {transcript.modes.availableModes.map((mode) => {
                  const checked = mode.id === transcript.modes?.currentModeId;
                  return (
                    <button
                      key={mode.id}
                      type="button"
                      role="menuitemradio"
                      aria-checked={checked}
                      disabled={prompting}
                      className={`agent-popover-row${checked ? " is-checked" : ""}`}
                      onClick={() => {
                        if (sessionId)
                          void call("/api/agent/mode", { session: sessionId, mode: mode.id });
                        setActivePopover(null);
                      }}
                    >
                      <span className="agent-popover-check">
                        {checked && <Icon name="tick" size={14} />}
                      </span>
                      <span className="agent-popover-main">{mode.name}</span>
                      {mode.description && (
                        <span className="agent-popover-sub">{mode.description}</span>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <div className="agent-popover-footer is-note">
            {t("工作区边界：{scope}", {
              scope: t(workspace === "project" ? "当前工程目录" : "独立隔离缓存目录"),
            })}
          </div>
        </FloatingPopover>

        {/* Context Usage Details Popover */}
        {usage && (
          <FloatingPopover
            anchorRef={usageBtnRef}
            open={activePopover === "usage"}
            onClose={() => setActivePopover(null)}
            label={t("上下文用量")}
            className="agent-popover-usage"
          >
            <div className="agent-usage-details">
              <div className="agent-popover-heading">{t("上下文用量")}</div>
              <p>
                {t("上下文已用 {used} / {size} tokens", {
                  used: usage.used.toLocaleString(),
                  size: usage.size.toLocaleString(),
                })}
              </p>
              <div className="agent-usage-bar">
                <span
                  style={{
                    width: `${Math.min(100, Math.max(2, (usage.used / usage.size) * 100))}%`,
                  }}
                />
              </div>
            </div>
          </FloatingPopover>
        )}
      </div>
    </div>
  );

  return { actions, body, prompting, onHeaderContextMenu };
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

/** Dedicated right-side agent panel shared by empty and project workspaces. */
export function AgentPanel(props: AgentPanelProps) {
  const controller = useAgentPanel(props);
  return (
    <NavigatorPanel
      title={t("智能体")}
      actions={controller.actions}
      headingProps={{ onContextMenu: controller.onHeaderContextMenu, translate: "no" }}
      className="agent-navigator-panel"
    >
      {controller.body}
    </NavigatorPanel>
  );
}
