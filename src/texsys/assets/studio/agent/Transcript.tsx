/** Conversation rendering for the agent panel (DDR-0033 §5). */
import React, { useMemo, useState } from "react";
import { Icon } from "../ui";
import { MarkdownPreview } from "../MarkdownPreview";
import { t } from "../i18n";
import {
  diffStats,
  lineDiff,
  presentTool,
  type TerminalRecord,
  type Transcript,
  type TranscriptItem,
} from "../agent-model";
import { Chip, IconButton } from "./primitives";

type OpenFile = (path: string, line?: number) => void;

function Chevron() {
  return (
    <span className="ag-chevron" aria-hidden="true">
      <Icon name="chevron-right" size={12} />
    </span>
  );
}

function DiffLines({ oldText, newText }: { oldText: string; newText: string }) {
  const lines = useMemo(() => lineDiff(oldText, newText), [oldText, newText]);
  return (
    <pre className="ag-diff-lines" aria-label={t("差异")}>
      {lines.map((line, index) =>
        line.type === "gap" ? (
          <span key={index} className="ag-diff-gap">
            ⋯
          </span>
        ) : (
          <span key={index} className={`ag-diff-${line.type}`}>
            <i aria-hidden="true">{line.type === "add" ? "+" : line.type === "del" ? "−" : " "}</i>
            {line.text || " "}
          </span>
        ),
      )}
    </pre>
  );
}

function Diff({
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
  openFile?: OpenFile;
}) {
  const stats = useMemo(() => diffStats(lineDiff(oldText, newText, 0)), [oldText, newText]);
  return (
    <details className="ag-diff">
      <summary>
        <Chevron />
        <Icon name="file" size={13} />
        <span className="ag-diff-path" title={path}>
          {path.split("/").at(-1)}
        </span>
        {created && <span className="ag-tag">{t("新建")}</span>}
        {partial && <span className="ag-tag">{t("片段")}</span>}
        <span className="ag-diff-stat">
          <ins>+{stats.added}</ins> <del>−{stats.removed}</del>
        </span>
        {openFile && !path.startsWith("/") && (
          <button
            type="button"
            className="ag-link"
            onClick={(event) => {
              event.preventDefault();
              openFile(path);
            }}
          >
            {t("打开")}
          </button>
        )}
      </summary>
      <DiffLines oldText={oldText} newText={newText} />
    </details>
  );
}

function TerminalBlock({ terminal }: { terminal?: TerminalRecord }) {
  if (!terminal) return null;
  const exit = terminal.exitStatus;
  return (
    <div>
      <div className="ag-terminal-head">
        <Icon name="terminal" size={12} />
        <code>{terminal.command}</code>
        {exit && (
          <span className={exit.exitCode === 0 ? "ag-ok" : "ag-bad"}>
            {exit.signal ? exit.signal : t("退出码 {code}", { code: String(exit.exitCode) })}
          </span>
        )}
      </div>
      {terminal.output && (
        <pre className="ag-pre">
          {terminal.truncated ? "…\n" : ""}
          {terminal.output.length > 12000 ? "…" + terminal.output.slice(-12000) : terminal.output}
        </pre>
      )}
    </div>
  );
}

function ToolStatus({ status }: { status: string }) {
  if (status === "in_progress" || status === "pending")
    return <span className="ag-spinner" role="img" aria-label={t("进行中")} />;
  if (status === "failed") return <Icon name="error-circle" size={14} />;
  return <Icon name="tick" size={14} />;
}

const clip = (value: string, limit = 8000) =>
  value.length > limit ? value.slice(0, limit) + "\n…" : value;

function ToolRow({
  item,
  root,
  terminals,
  openFile,
}: {
  item: Extract<TranscriptItem, { kind: "tool" }>;
  root: string;
  terminals: Record<string, TerminalRecord>;
  openFile?: OpenFile;
}) {
  const view = presentTool(item, root);
  const commandBlock = Boolean(view.command && view.terminals.length === 0);
  const hasBody =
    view.diffs.length > 0 || view.text.length > 0 || view.terminals.length > 0 || commandBlock;
  const head = (
    <span className="ag-tool-head">
      <span className="ag-tool-status">
        <ToolStatus status={item.status} />
      </span>
      <span className="ag-tool-title">{view.title}</span>
      {view.detail &&
        (view.path && openFile && !view.command ? (
          <button
            type="button"
            className="ag-tool-detail"
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
          <code className="ag-tool-detail" title={view.detail}>
            {view.detail}
          </code>
        ))}
      {hasBody && <Chevron />}
    </span>
  );
  const cls = `ag-tool is-${item.status}`;
  if (!hasBody) return <div className={cls}>{head}</div>;
  return (
    <details className={cls} open={item.status === "failed"}>
      <summary>{head}</summary>
      <div className="ag-tool-body">
        {commandBlock && (
          <div>
            <div className="ag-terminal-head">
              <Icon name="terminal" size={12} />
              <code>{view.command}</code>
            </div>
            {view.text.map((value, index) => (
              <pre key={index} className="ag-pre">
                {clip(value)}
              </pre>
            ))}
          </div>
        )}
        {view.diffs.map((diff, index) => (
          <Diff key={index} {...diff} openFile={openFile} />
        ))}
        {view.terminals.map((id) => (
          <TerminalBlock key={id} terminal={terminals[id]} />
        ))}
        {!commandBlock &&
          view.text.map((value, index) => (
            <pre key={index} className="ag-pre">
              {clip(value)}
            </pre>
          ))}
      </div>
    </details>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <IconButton
      label={t(copied ? "已复制" : "复制回答")}
      icon={copied ? "tick" : "copy"}
      size={13}
      onClick={() => {
        void navigator.clipboard?.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1400);
      }}
    />
  );
}

const stopReasons: Record<string, string> = {
  cancelled: "已停止本轮处理。",
  max_tokens: "已达到模型输出上限。",
  max_turn_requests: "已达到单轮请求上限。",
  refusal: "智能体拒绝继续处理。",
  error: "本轮处理失败。",
};

export function TranscriptView({
  transcript,
  root,
  prompting,
  openFile,
  respond,
}: {
  transcript: Transcript;
  root: string;
  prompting: boolean;
  openFile?: OpenFile;
  respond: (key: string, option: string | null) => void;
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
  let lastAgent = -1;
  transcript.items.forEach((item, index) => {
    if (item.kind === "agent" && item.text.trim()) lastAgent = index;
  });
  return (
    <>
      {transcript.items.map((item, index) => {
        switch (item.kind) {
          case "user":
            return (
              <div key={item.key} className="ag-user">
                <p>{item.text}</p>
                {item.context.length > 0 && (
                  <div className="ag-chips">
                    {item.context.map((path) => (
                      <Chip
                        key={path}
                        label={path.split("/").at(-1) ?? path}
                        title={path}
                        onClick={() => openFile?.(path)}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          case "agent":
            if (!item.text.trim()) return null;
            return (
              <div
                key={item.key}
                className={`ag-message${index === lastAgent && !prompting ? " is-latest" : ""}`}
              >
                <MarkdownPreview content={item.text} onOpenFile={(path) => openFile?.(path)} />
                <div className="ag-message-actions">
                  <CopyButton text={item.text} />
                </div>
              </div>
            );
          case "thought": {
            if (!item.text.trim()) return null;
            const streaming = prompting && index === transcript.items.length - 1;
            return (
              <details key={item.key} className={`ag-thought${streaming ? " is-streaming" : ""}`}>
                <summary>
                  <Chevron />
                  <span>{t(streaming ? "正在思考" : "思考过程")}</span>
                </summary>
                <div className="ag-thought-body">{item.text}</div>
              </details>
            );
          }
          case "tool":
            return (
              <ToolRow
                key={item.key}
                item={item}
                root={root}
                terminals={transcript.terminals}
                openFile={openFile}
              />
            );
          case "plan":
            return (
              <div key={item.key} className="ag-plan">
                <div className="ag-section-title">
                  <Icon name="list" size={13} />
                  {t("计划")}
                </div>
                <ol>
                  {item.entries.map((entry, position) => (
                    <li key={position} className={`is-${entry.status ?? "pending"}`}>
                      {entry.content}
                    </li>
                  ))}
                </ol>
              </div>
            );
          case "permission":
            return item.settled ? (
              <p key={item.key} className="ag-permission is-settled">
                {item.optionId
                  ? t("已选择：{option}", {
                      option:
                        item.options.find((option) => option.optionId === item.optionId)?.name ??
                        item.optionId,
                    })
                  : t("授权请求已取消。")}
              </p>
            ) : (
              <div key={item.key} className="ag-permission">
                <div className="ag-permission-title">
                  <Icon name="shield" size={14} />
                  {t("智能体请求授权")}
                </div>
                {item.title && <p>{item.title}</p>}
                <div className="ag-permission-actions">
                  {item.options.map((option) => (
                    <button
                      key={option.optionId}
                      type="button"
                      className={`ag-button${option.kind?.startsWith("allow") ? " is-primary" : ""}`}
                      onClick={() => respond(item.requestKey, option.optionId)}
                    >
                      {option.name}
                    </button>
                  ))}
                </div>
              </div>
            );
          case "file":
            return item.newText != null ? (
              <Diff
                key={item.key}
                path={item.path}
                oldText={item.oldText ?? ""}
                newText={item.newText}
                created={item.created}
                openFile={openFile}
              />
            ) : (
              <div key={item.key} className="ag-section-title">
                <Icon name="edit" size={13} /> {item.path}
              </div>
            );
          case "turn":
            return item.stopReason !== "end_turn" ? (
              <p
                key={item.key}
                className={`ag-turn${item.stopReason === "error" ? " is-error" : ""}`}
              >
                {t(stopReasons[item.stopReason] ?? "本轮处理结束。")}
                {item.error ? ` ${item.error}` : ""}
              </p>
            ) : null;
          case "notice":
            return (
              <div key={item.key} className="ag-notice" role="alert">
                <pre>{item.text}</pre>
              </div>
            );
          case "history":
            return (
              <div key={item.key} className="ag-history-divider" role="separator">
                {t("以上为历史记录")}
              </div>
            );
        }
        return null;
      })}
      {Object.entries(transcript.terminals)
        .filter(([id]) => !terminalTools.has(id))
        .map(([id, terminal]) => (
          <TerminalBlock key={id} terminal={terminal} />
        ))}
    </>
  );
}
