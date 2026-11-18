/** Pure state for the agent panel: ACP session events folded into a transcript. */

export type AgentInfo = {
  id: string;
  name: string;
  description: string;
  command: string;
  source: "builtin" | "local";
  available: boolean;
  reason: string;
  env: string[];
};

export type AgentAuthMethod = { id: string; name: string; description?: string };

export type AgentSessionSummary = {
  id: string;
  agent: string;
  agent_name: string;
  agent_version: string;
  command?: string;
  cwd?: string;
  title: string;
  state: AgentSessionState;
  error: string;
  created_at: number;
  seq?: number;
  stderr?: string;
  auth_methods: AgentAuthMethod[];
  capabilities: Record<string, unknown>;
};

export type AgentSessionState =
  "starting" | "ready" | "prompting" | "auth_required" | "error" | "exited" | "closed";

export type AgentServiceState = {
  agents: AgentInfo[];
  sessions: AgentSessionSummary[];
  config_path: string;
  diagnostics: string[];
  root: string;
};

export type AgentEvent = { seq: number; type: string } & Record<string, unknown>;

export type AgentEventBatch = {
  session: AgentSessionSummary;
  events: AgentEvent[];
  seq: number;
  reset: boolean;
};

export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "resource_link"; uri: string; name?: string; title?: string }
  | { type: "resource"; resource: { uri: string; text?: string } }
  | { type: "image"; mimeType?: string; data?: string; uri?: string }
  | { type: string; [key: string]: unknown };

export type ToolContent =
  | { type: "content"; content: ContentBlock }
  | { type: "diff"; path: string; oldText?: string | null; newText: string }
  | { type: "terminal"; terminalId: string };

export type ToolLocation = { path: string; line?: number | null };

export type ToolStatus = "pending" | "in_progress" | "completed" | "failed";

export type PlanEntry = { content: string; priority?: string; status?: string };

export type PermissionOption = {
  optionId: string;
  name: string;
  kind?: "allow_once" | "allow_always" | "reject_once" | "reject_always" | string;
};

export type ConfigOptionValue = { value: string; name: string; description?: string };
export type ConfigOptionGroup = { group: string; name: string; options: ConfigOptionValue[] };
export type ConfigOption = {
  id: string;
  name: string;
  category?: string;
  type: string;
  currentValue: string;
  options: (ConfigOptionValue | ConfigOptionGroup)[];
};

export type SessionMode = { id: string; name: string; description?: string };
export type SessionModel = { modelId: string; name: string; description?: string };
export type AvailableCommand = { name: string; description?: string; input?: unknown };
export type AgentDebugEntry = { seq: number; kind: string; detail: string };

export type TranscriptItem =
  | { kind: "user"; key: string; text: string; context: string[] }
  | { kind: "agent"; key: string; messageId?: string; text: string }
  | { kind: "thought"; key: string; messageId?: string; text: string }
  | {
      kind: "tool";
      key: string;
      id: string;
      title: string;
      toolKind: string;
      status: ToolStatus;
      rawInput?: unknown;
      rawOutput?: unknown;
      content: ToolContent[];
      locations: ToolLocation[];
    }
  | { kind: "plan"; key: string; entries: PlanEntry[] }
  | {
      kind: "permission";
      key: string;
      requestKey: string;
      title: string;
      toolCallId?: string;
      options: PermissionOption[];
      settled: boolean;
      optionId?: string | null;
    }
  | {
      kind: "file";
      key: string;
      path: string;
      created: boolean;
      oldText?: string | null;
      newText?: string | null;
    }
  | { kind: "turn"; key: string; stopReason: string; error?: string }
  | { kind: "notice"; key: string; tone: "error" | "info"; text: string };

export type TerminalRecord = {
  command: string;
  cwd: string;
  output: string;
  truncated: boolean;
  exitStatus?: { exitCode: number | null; signal: string | null };
};

export type Transcript = {
  seq: number;
  items: TranscriptItem[];
  terminals: Record<string, TerminalRecord>;
  state: AgentSessionState;
  error: string;
  configOptions: ConfigOption[];
  models?: { currentModelId: string; availableModels: SessionModel[] };
  modes?: { currentModeId: string; availableModes: SessionMode[] };
  commands: AvailableCommand[];
  usage?: { used: number; size: number };
  changed: string[];
  /** Incremented when the agent changed project files and the workspace should refresh. */
  fileChanges: number;
  debugLog: AgentDebugEntry[];
};

export const emptyTranscript = (): Transcript => ({
  seq: 0,
  items: [],
  terminals: {},
  state: "starting",
  error: "",
  configOptions: [],
  commands: [],
  changed: [],
  fileChanges: 0,
  debugLog: [],
});

const editKinds = new Set(["edit", "delete", "move"]);
const editTitles = new Set(["edit", "write", "multiedit", "apply_patch"]);
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown) => (typeof value === "string" ? value : "");

/** Project-relative path for an absolute agent path, or undefined outside the project. */
export function projectPath(path: string, root: string): string | undefined {
  if (!path) return undefined;
  if (!path.startsWith("/")) return path.replace(/^\.\//, "") || undefined;
  const base = root.replace(/\/+$/, "");
  if (path === base) return undefined;
  return path.startsWith(base + "/") ? path.slice(base.length + 1) : undefined;
}

function blockText(block: unknown) {
  if (!isRecord(block)) return "";
  if (block.type === "text") return text(block.text);
  if (block.type === "resource_link")
    return `[${text(block.name) || text(block.uri)}](${text(block.uri)})`;
  return "";
}

function rawInputPaths(rawInput: unknown): string[] {
  if (!isRecord(rawInput)) return [];
  return ["file_path", "path", "filePath", "target"]
    .map((key) => text(rawInput[key]))
    .filter(Boolean);
}

function addChanged(changed: string[], path: string | undefined) {
  return path && !changed.includes(path) ? [...changed, path] : changed;
}

function mergeTool(
  previous: Extract<TranscriptItem, { kind: "tool" }> | undefined,
  update: Record<string, unknown>,
  key: string,
): Extract<TranscriptItem, { kind: "tool" }> {
  const base = previous ?? {
    kind: "tool" as const,
    key,
    id: text(update.toolCallId),
    title: "",
    toolKind: "other",
    status: "pending" as ToolStatus,
    content: [],
    locations: [],
  };
  return {
    ...base,
    title: text(update.title) || base.title,
    toolKind: text(update.kind) || base.toolKind,
    status: (text(update.status) as ToolStatus) || base.status,
    rawInput: update.rawInput ?? base.rawInput,
    rawOutput: update.rawOutput ?? base.rawOutput,
    content: Array.isArray(update.content) ? (update.content as ToolContent[]) : base.content,
    locations: Array.isArray(update.locations)
      ? (update.locations as ToolLocation[])
      : base.locations,
  };
}

function lastTurnStart(items: TranscriptItem[]) {
  for (let index = items.length - 1; index >= 0; index -= 1)
    if (items[index].kind === "user") return index;
  return -1;
}

function applyUpdate(next: Transcript, update: Record<string, unknown>, seq: number, root: string) {
  const kind = text(update.sessionUpdate);
  const items = next.items;
  if (
    kind === "agent_message_chunk" ||
    kind === "agent_thought_chunk" ||
    kind === "user_message_chunk"
  ) {
    const chunk = blockText(update.content);
    const itemKind =
      kind === "agent_message_chunk"
        ? "agent"
        : kind === "agent_thought_chunk"
          ? "thought"
          : "user";
    const messageId = text(update.messageId) || undefined;
    const last = items.at(-1);
    const lastId = last && "messageId" in last ? last.messageId : undefined;
    const sameMessage = !messageId || !lastId || lastId === messageId;
    if (last && last.kind === itemKind && (itemKind === "user" || sameMessage)) {
      items[items.length - 1] = { ...last, text: last.text + chunk } as TranscriptItem;
    } else if (itemKind === "user") {
      items.push({ kind: "user", key: `u${seq}`, text: chunk, context: [] });
    } else {
      items.push({ kind: itemKind, key: `m${seq}`, messageId, text: chunk });
    }
    return;
  }
  if (kind === "tool_call" || kind === "tool_call_update") {
    const id = text(update.toolCallId);
    const index = items.findIndex((item) => item.kind === "tool" && item.id === id);
    const previous =
      index >= 0 ? (items[index] as Extract<TranscriptItem, { kind: "tool" }>) : undefined;
    const merged = mergeTool(previous, update, `t${seq}`);
    if (index >= 0) items[index] = merged;
    else items.push(merged);
    const editing =
      editKinds.has(merged.toolKind) || editTitles.has(merged.title.trim().toLowerCase());
    if (merged.status === "completed" && editing) {
      for (const location of merged.locations)
        next.changed = addChanged(next.changed, projectPath(location.path, root));
      for (const content of merged.content)
        if (content.type === "diff")
          next.changed = addChanged(next.changed, projectPath(content.path, root));
      for (const path of rawInputPaths(merged.rawInput))
        next.changed = addChanged(next.changed, projectPath(path, root));
    }
    if (merged.status === "completed" || merged.status === "failed") next.fileChanges += 1;
    return;
  }
  if (kind === "plan") {
    const entries = Array.isArray(update.entries) ? (update.entries as PlanEntry[]) : [];
    const start = lastTurnStart(items);
    const index = items.findIndex((item, position) => item.kind === "plan" && position > start);
    if (index >= 0)
      items[index] = { ...(items[index] as Extract<TranscriptItem, { kind: "plan" }>), entries };
    else items.push({ kind: "plan", key: `p${seq}`, entries });
    return;
  }
  if (kind === "available_commands_update") {
    next.commands = Array.isArray(update.availableCommands)
      ? (update.availableCommands as AvailableCommand[])
      : [];
    return;
  }
  if (kind === "current_mode_update" && next.modes) {
    next.modes = {
      ...next.modes,
      currentModeId: text(update.currentModeId) || next.modes.currentModeId,
    };
    return;
  }
  if (kind === "current_model_update" && next.models) {
    next.models = {
      ...next.models,
      currentModelId: text(update.currentModelId) || next.models.currentModelId,
    };
    return;
  }
  if (
    (kind === "config_option_update" || kind === "config_options_update") &&
    Array.isArray(update.configOptions)
  ) {
    next.configOptions = update.configOptions as ConfigOption[];
    return;
  }
  if (
    kind === "usage_update" &&
    typeof update.used === "number" &&
    typeof update.size === "number"
  ) {
    next.usage = { used: update.used, size: update.size };
  }
}

function appendDebug(log: AgentDebugEntry[], seq: number, event: AgentEvent) {
  let detail = "";
  switch (event.type) {
    case "state":
      detail = `${text(event.state)}${event.error ? ` (${text(event.error).slice(0, 80)})` : ""}`;
      break;
    case "initialized": {
      const info = isRecord(event.agent) ? event.agent : {};
      detail = [text(info.title) || text(info.name), text(info.version)].filter(Boolean).join(" ");
      break;
    }
    case "configured":
      detail = `options=${Array.isArray(event.configOptions) ? event.configOptions.length : 0}`;
      break;
    case "user":
      detail = text(event.text).slice(0, 60);
      break;
    case "update": {
      const u = isRecord(event.update) ? event.update : {};
      const kind = text(u.sessionUpdate);
      if (kind.endsWith("_chunk")) return log;
      detail = [kind, text(u.title) || text(u.currentModeId) || text(u.currentModelId)]
        .filter(Boolean)
        .join(" ");
      break;
    }
    case "permission": {
      const tc = isRecord(event.toolCall) ? event.toolCall : {};
      detail = text(tc.title) || text(event.key);
      break;
    }
    case "permission_resolved":
      detail = text(event.optionId) || "cancelled";
      break;
    case "fs":
      detail = `${text(event.op)} ${text(event.path)}`;
      break;
    case "terminal":
      detail = `${text(event.terminalId)} ${text(event.command)}`.trim();
      break;
    case "turn_end":
      detail = text(event.stopReason);
      break;
  }
  const next = [...log, { seq, kind: event.type, detail }];
  return next.length > 120 ? next.slice(-120) : next;
}

/** Fold ordered session events into a new transcript without mutating the input. */
export function applyAgentEvents(
  previous: Transcript,
  events: AgentEvent[],
  root: string,
  reset = false,
): Transcript {
  const next: Transcript = reset
    ? emptyTranscript()
    : {
        ...previous,
        items: [...previous.items],
        terminals: { ...previous.terminals },
        debugLog: [...previous.debugLog],
      };
  for (const event of events) {
    if (event.seq <= next.seq) continue;
    next.seq = event.seq;
    const seq = event.seq;
    next.debugLog = appendDebug(next.debugLog, seq, event);
    switch (event.type) {
      case "state":
        next.state = event.state as AgentSessionState;
        next.error = text(event.error);
        if (next.error && ["error", "exited", "auth_required"].includes(next.state))
          next.items.push({ kind: "notice", key: `n${seq}`, tone: "error", text: next.error });
        break;
      case "configured":
        if (Array.isArray(event.configOptions))
          next.configOptions = event.configOptions as ConfigOption[];
        if (isRecord(event.modes) && Array.isArray(event.modes.availableModes))
          next.modes = event.modes as Transcript["modes"];
        if (isRecord(event.models) && Array.isArray(event.models.availableModels))
          next.models = event.models as Transcript["models"];
        break;
      case "user":
        next.items.push({
          kind: "user",
          key: `u${seq}`,
          text: text(event.text),
          context: Array.isArray(event.context) ? (event.context as string[]) : [],
        });
        break;
      case "update":
        if (isRecord(event.update)) applyUpdate(next, event.update, seq, root);
        break;
      case "permission": {
        const toolCall = isRecord(event.toolCall) ? event.toolCall : {};
        next.items.push({
          kind: "permission",
          key: `r${seq}`,
          requestKey: text(event.key),
          title: text(toolCall.title),
          toolCallId: text(toolCall.toolCallId) || undefined,
          options: Array.isArray(event.options) ? (event.options as PermissionOption[]) : [],
          settled: false,
        });
        break;
      }
      case "permission_resolved": {
        const index = next.items.findIndex(
          (item) => item.kind === "permission" && item.requestKey === event.key,
        );
        if (index >= 0)
          next.items[index] = {
            ...(next.items[index] as Extract<TranscriptItem, { kind: "permission" }>),
            settled: true,
            optionId: (event.optionId as string | null) ?? null,
          };
        break;
      }
      case "fs":
        if (event.op === "write") {
          const path = text(event.path);
          next.changed = addChanged(next.changed, path);
          next.fileChanges += 1;
          next.items.push({
            kind: "file",
            key: `f${seq}`,
            path,
            created: event.created === true,
            oldText: event.oldText as string | null | undefined,
            newText: event.newText as string | null | undefined,
          });
        }
        break;
      case "terminal": {
        const id = text(event.terminalId);
        const current = next.terminals[id] ?? {
          command: "",
          cwd: "",
          output: "",
          truncated: false,
        };
        next.terminals[id] = {
          command: text(event.command) || current.command,
          cwd: text(event.cwd) || current.cwd,
          output: typeof event.output === "string" ? event.output : current.output,
          truncated: event.truncated === true,
          exitStatus: (event.exitStatus as TerminalRecord["exitStatus"]) ?? current.exitStatus,
        };
        if (event.exitStatus) next.fileChanges += 1;
        break;
      }
      case "turn_end":
        next.items.push({
          kind: "turn",
          key: `e${seq}`,
          stopReason: text(event.stopReason) || "end_turn",
          error: text(event.error) || undefined,
        });
        next.fileChanges += 1;
        break;
    }
  }
  return next;
}

export type DiffLine = { type: "same" | "add" | "del" | "gap"; text: string };

/** Line diff with collapsed context; bounded so large rewrites degrade to replace. */
export function lineDiff(oldText: string, newText: string, context = 2): DiffLine[] {
  const a = oldText ? oldText.replace(/\n$/, "").split("\n") : [];
  const b = newText ? newText.replace(/\n$/, "").split("\n") : [];
  let prefix = 0;
  while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix]) prefix += 1;
  let suffix = 0;
  while (
    suffix < a.length - prefix &&
    suffix < b.length - prefix &&
    a[a.length - 1 - suffix] === b[b.length - 1 - suffix]
  )
    suffix += 1;
  const midA = a.slice(prefix, a.length - suffix);
  const midB = b.slice(prefix, b.length - suffix);
  const middle: DiffLine[] = [];
  if (midA.length * midB.length > 1_000_000) {
    middle.push(...midA.map((line) => ({ type: "del" as const, text: line })));
    middle.push(...midB.map((line) => ({ type: "add" as const, text: line })));
  } else {
    const rows = midA.length + 1;
    const cols = midB.length + 1;
    const table = new Uint32Array(rows * cols);
    for (let i = midA.length - 1; i >= 0; i -= 1)
      for (let j = midB.length - 1; j >= 0; j -= 1)
        table[i * cols + j] =
          midA[i] === midB[j]
            ? table[(i + 1) * cols + j + 1] + 1
            : Math.max(table[(i + 1) * cols + j], table[i * cols + j + 1]);
    let i = 0;
    let j = 0;
    while (i < midA.length || j < midB.length) {
      if (i < midA.length && j < midB.length && midA[i] === midB[j]) {
        middle.push({ type: "same", text: midA[i] });
        i += 1;
        j += 1;
      } else if (
        j < midB.length &&
        (i === midA.length || table[i * cols + j + 1] >= table[(i + 1) * cols + j])
      ) {
        middle.push({ type: "add", text: midB[j] });
        j += 1;
      } else {
        middle.push({ type: "del", text: midA[i] });
        i += 1;
      }
    }
  }
  const all: DiffLine[] = [
    ...a.slice(0, prefix).map((line) => ({ type: "same" as const, text: line })),
    ...middle,
    ...a.slice(a.length - suffix).map((line) => ({ type: "same" as const, text: line })),
  ];
  const keep = all.map(
    (line, index) =>
      line.type !== "same" ||
      all
        .slice(Math.max(0, index - context), index + context + 1)
        .some((near) => near.type !== "same"),
  );
  const result: DiffLine[] = [];
  all.forEach((line, index) => {
    if (keep[index]) result.push(line);
    else if (result.at(-1)?.type !== "gap") result.push({ type: "gap", text: "" });
  });
  return result;
}

export function diffStats(lines: DiffLine[]) {
  return {
    added: lines.filter((line) => line.type === "add").length,
    removed: lines.filter((line) => line.type === "del").length,
  };
}

export type ToolPresentation = {
  icon: string;
  title: string;
  detail?: string;
  path?: string;
  line?: number;
  command?: string;
  diffs: { path: string; oldText: string; newText: string; partial: boolean }[];
  text: string[];
  terminals: string[];
};

const kindIcons: Record<string, string> = {
  read: "file",
  edit: "edit",
  delete: "trash",
  move: "replace",
  search: "search",
  execute: "terminal",
  think: "sparkles",
  fetch: "globe",
  switch_mode: "settings",
  other: "module",
};
const titleKinds: Record<string, string> = {
  read: "read",
  view: "read",
  cat: "read",
  edit: "edit",
  write: "edit",
  multiedit: "edit",
  bash: "execute",
  shell: "execute",
  exec: "execute",
  grep: "search",
  glob: "search",
  find: "search",
  search: "search",
  fetch: "fetch",
  webfetch: "fetch",
};

function rawOutputTexts(rawOutput: unknown): string[] {
  if (typeof rawOutput === "string") return rawOutput.trim() ? [rawOutput] : [];
  if (Array.isArray(rawOutput))
    return rawOutput
      .map((entry) => (typeof entry === "string" ? entry : blockText(entry)))
      .filter(Boolean);
  if (!isRecord(rawOutput)) return [];
  const parts: string[] = [];
  for (const key of [
    "output",
    "stdout",
    "stderr",
    "content",
    "text",
    "result",
    "error",
    "message",
  ]) {
    const value = rawOutput[key];
    if (typeof value === "string" && value.trim()) parts.push(value);
    else if (Array.isArray(value)) {
      const nested = value
        .map((entry) => (typeof entry === "string" ? entry : blockText(entry)))
        .filter(Boolean)
        .join("\n");
      if (nested) parts.push(nested);
    }
  }
  return parts;
}

/** Normalize a tool call for display, including agents that only send raw inputs. */
export function presentTool(
  item: Extract<TranscriptItem, { kind: "tool" }>,
  root: string,
): ToolPresentation {
  const raw = isRecord(item.rawInput) ? item.rawInput : {};
  const lowerTitle = item.title.trim().toLowerCase();
  const kind = item.toolKind !== "other" ? item.toolKind : (titleKinds[lowerTitle] ?? "other");
  const location = item.locations[0];
  const absolute = location?.path || rawInputPaths(raw)[0] || "";
  const path = absolute ? (projectPath(absolute, root) ?? absolute) : undefined;
  const command =
    text(raw.command) || (Array.isArray(raw.command) ? (raw.command as string[]).join(" ") : "");
  const diffs: ToolPresentation["diffs"] = [];
  for (const content of item.content)
    if (content.type === "diff")
      diffs.push({
        path: projectPath(content.path, root) ?? content.path,
        oldText: content.oldText ?? "",
        newText: content.newText,
        partial: false,
      });
  if (!diffs.length && path && kind === "edit") {
    if (typeof raw.old_string === "string" || typeof raw.new_string === "string")
      diffs.push({
        path,
        oldText: text(raw.old_string),
        newText: text(raw.new_string),
        partial: true,
      });
    else if (typeof raw.content === "string")
      diffs.push({ path, oldText: "", newText: raw.content, partial: false });
  }
  const contentTexts = item.content
    .filter(
      (content): content is Extract<ToolContent, { type: "content" }> => content.type === "content",
    )
    .map((content) => blockText(content.content))
    .filter(Boolean);
  const terminals = item.content
    .filter(
      (content): content is Extract<ToolContent, { type: "terminal" }> =>
        content.type === "terminal",
    )
    .map((content) => content.terminalId);
  const textContent =
    contentTexts.length > 0 || terminals.length > 0 ? contentTexts : rawOutputTexts(item.rawOutput);
  const pattern = text(raw.pattern) || text(raw.query);
  return {
    icon: kindIcons[kind] ?? "module",
    title: item.title || kind,
    detail: command || path || pattern || text(raw.url) || text(raw.description) || undefined,
    path: path && !path.startsWith("/") ? path : undefined,
    line: location?.line ?? undefined,
    command: command || undefined,
    diffs,
    text: textContent,
    terminals,
  };
}

/** Flatten grouped ACP select options. */
export function configChoices(option: ConfigOption): ConfigOptionValue[] {
  return option.options.flatMap((entry) =>
    "options" in entry && Array.isArray(entry.options)
      ? entry.options
      : [entry as ConfigOptionValue],
  );
}

/** Clean a provider or harness label without redundant "(ACP)" or "Harness" noise. */
export function cleanProviderName(raw: string): string {
  const trimmed = raw
    .trim()
    .replace(/\s*(?:\(ACP\)|\[ACP\])\s*$/i, "")
    .trim();
  const stripped = trimmed.replace(/\s+(?:Harness|CLI)$/i, "").trim();
  return stripped || trimmed || "Models";
}

function inferFamilyProvider(modelName: string): string | undefined {
  const s = modelName.trim();
  if (/^deepseek/i.test(s)) return "DeepSeek";
  if (/^claude/i.test(s)) return "Anthropic";
  if (/^(?:gpt-|o[134](?:$|-|\b)|codex|chatgpt)/i.test(s)) return "OpenAI";
  if (/^gemini/i.test(s)) return "Google";
  if (/^(?:kimi|moonshot)/i.test(s)) return "Kimi";
  if (/^qwen/i.test(s)) return "Qwen";
  if (/^glm/i.test(s)) return "Zhipu";
  if (/^minimax/i.test(s)) return "MiniMax";
  return undefined;
}

/** Strip provider suffixes, route prefixes, and ACP noise from a model's display name. */
export function cleanModelName(
  rawName: string,
  rawValue?: string,
): { name: string; inferredProvider?: string } {
  let inferredProvider: string | undefined;
  let candidate = rawName.trim();

  for (const probe of [rawValue, candidate]) {
    if (!probe || !probe.startsWith("[")) continue;
    try {
      const parsed = JSON.parse(probe) as unknown;
      if (
        Array.isArray(parsed) &&
        parsed.length >= 2 &&
        typeof parsed[0] === "string" &&
        typeof parsed[1] === "string"
      ) {
        inferredProvider ??= parsed[0].trim() || undefined;
        if (probe === candidate) candidate = parsed[1].trim();
      }
    } catch {
      // Plain model identifier.
    }
  }

  const sepMatch = /^(.+?)\s+[·•]\s+(.+)$/.exec(candidate);
  if (sepMatch) {
    candidate = sepMatch[1].trim();
    inferredProvider ??= cleanProviderName(sepMatch[2]);
  }

  candidate = candidate.replace(/\s*(?:\(ACP\)|\[ACP\])\s*$/i, "").trim();

  if (rawValue && !rawValue.startsWith("[") && rawValue.includes("/") && !rawValue.includes(" ")) {
    const firstSlash = rawValue.indexOf("/");
    const prefix = rawValue.slice(0, firstSlash).trim();
    if (prefix) inferredProvider ??= prefix;
  }

  if (candidate.includes("/") && !candidate.includes(" ")) {
    const parts = candidate.split("/").filter(Boolean);
    if (parts.length >= 2) {
      inferredProvider ??= parts[0];
      candidate = parts.at(-1)!;
    }
  }

  inferredProvider ??= inferFamilyProvider(candidate);
  return { name: candidate || rawName.trim(), inferredProvider };
}

export function isModelOption(option: ConfigOption): boolean {
  return (
    option.type === "select" &&
    (option.category === "model" || option.id === "model" || /^model$/i.test(option.name.trim()))
  );
}

export function isReasoningOption(option: ConfigOption): boolean {
  return (
    option.type === "select" &&
    !isModelOption(option) &&
    (option.category === "thought_level" ||
      option.category === "reasoning" ||
      /reason|effort|thought|thinking|推理|思考/i.test(`${option.id} ${option.name}`))
  );
}

export function isContextWindowOption(option: ConfigOption): boolean {
  return (
    option.type === "select" &&
    !isModelOption(option) &&
    !isReasoningOption(option) &&
    (option.category === "context" ||
      option.category === "context_window" ||
      option.category === "tokens" ||
      /context|window|num_ctx|max_context|tokens|上下文/i.test(`${option.id} ${option.name}`))
  );
}

export type NormalizedModelChoice = ConfigOptionValue & {
  group: string;
  groupKey: string;
  agentId?: string;
};

export type ModelGroup = {
  key: string;
  label: string;
  options: NormalizedModelChoice[];
};

export type NormalizedModelCatalog = {
  source: "config" | "models";
  configId?: string;
  currentValue: string;
  currentLabel: string;
  choices: NormalizedModelChoice[];
  groups: ModelGroup[];
};

function appendGroupedChoice(groups: Map<string, ModelGroup>, item: NormalizedModelChoice) {
  let group = groups.get(item.groupKey);
  if (!group) {
    group = { key: item.groupKey, label: item.group, options: [] };
    groups.set(item.groupKey, group);
  }
  if (!group.options.some((existing) => existing.value === item.value)) {
    group.options.push(item);
  }
}

export function modelCatalog(
  transcript: Pick<Transcript, "configOptions" | "models">,
  fallbackProvider?: string,
  agentId?: string,
): NormalizedModelCatalog | undefined {
  const defaultGroup = cleanProviderName(fallbackProvider ?? "Models");
  const option = transcript.configOptions.find(isModelOption);
  if (option) {
    const groups = new Map<string, ModelGroup>();
    const choices: NormalizedModelChoice[] = [];
    for (const entry of option.options) {
      if ("options" in entry && Array.isArray(entry.options)) {
        const groupLabel = cleanProviderName(entry.name || entry.group || defaultGroup);
        const groupKey = (entry.group || groupLabel).trim().toLowerCase();
        for (const sub of entry.options) {
          const cleaned = cleanModelName(sub.name || sub.value, sub.value);
          const normalized: NormalizedModelChoice = {
            value: sub.value,
            name: cleaned.name,
            description: sub.description,
            group: groupLabel,
            groupKey,
            ...(agentId ? { agentId } : {}),
          };
          choices.push(normalized);
          appendGroupedChoice(groups, normalized);
        }
      } else {
        const sub = entry as ConfigOptionValue;
        const cleaned = cleanModelName(sub.name || sub.value, sub.value);
        const groupLabel = cleaned.inferredProvider ?? defaultGroup;
        const groupKey = groupLabel.trim().toLowerCase();
        const normalized: NormalizedModelChoice = {
          value: sub.value,
          name: cleaned.name,
          description: sub.description,
          group: groupLabel,
          groupKey,
          ...(agentId ? { agentId } : {}),
        };
        choices.push(normalized);
        appendGroupedChoice(groups, normalized);
      }
    }
    const current = choices.find((item) => item.value === option.currentValue);
    const currentLabel =
      current?.name ?? cleanModelName(option.currentValue, option.currentValue).name;
    return {
      source: "config",
      configId: option.id,
      currentValue: option.currentValue,
      currentLabel,
      choices,
      groups: [...groups.values()],
    };
  }
  if (transcript.models && transcript.models.availableModels.length > 0) {
    const groups = new Map<string, ModelGroup>();
    const choices: NormalizedModelChoice[] = transcript.models.availableModels.map((item) => {
      const cleaned = cleanModelName(item.name || item.modelId, item.modelId);
      const groupLabel = cleaned.inferredProvider ?? defaultGroup;
      const groupKey = groupLabel.trim().toLowerCase();
      const normalized: NormalizedModelChoice = {
        value: item.modelId,
        name: cleaned.name,
        description: item.description,
        group: groupLabel,
        groupKey,
        ...(agentId ? { agentId } : {}),
      };
      appendGroupedChoice(groups, normalized);
      return normalized;
    });
    const current = choices.find((item) => item.value === transcript.models?.currentModelId);
    const currentLabel =
      current?.name ??
      cleanModelName(transcript.models.currentModelId, transcript.models.currentModelId).name;
    return {
      source: "models",
      currentValue: transcript.models.currentModelId,
      currentLabel,
      choices,
      groups: [...groups.values()],
    };
  }
  return undefined;
}

/** Stack provider groups across sources without tabs or duplicate provider+model rows. */
export function mergeModelGroups(
  ...groupLists: (readonly ModelGroup[] | undefined)[]
): ModelGroup[] {
  const merged = new Map<string, ModelGroup>();
  const seenNames = new Set<string>();
  for (const list of groupLists) {
    if (!list) continue;
    for (const group of list) {
      let target = merged.get(group.key);
      if (!target) {
        target = { key: group.key, label: group.label, options: [] };
        merged.set(group.key, target);
      }
      for (const opt of group.options) {
        const dedupeKey = `${group.key}::${opt.name.trim().toLowerCase()}`;
        if (seenNames.has(dedupeKey)) continue;
        seenNames.add(dedupeKey);
        target.options.push(opt);
      }
    }
  }
  return [...merged.values()].filter((group) => group.options.length > 0);
}

const zhThinkingLabels: Record<string, string> = {
  off: "关闭",
  none: "关闭",
  disabled: "关闭",
  false: "关",
  on: "开启",
  enabled: "开启",
  true: "开",
  default: "默认",
  provider_default: "默认",
  "provider default": "默认",
  auto: "自动",
  minimal: "最低",
  low: "低",
  medium: "中",
  med: "中",
  standard: "标准",
  high: "高",
  xhigh: "超高",
  "very high": "超高",
  very_high: "超高",
  max: "最高",
  maximum: "最高",
  ultra: "极高",
};

const enThinkingLabels: Record<string, string> = {
  provider_default: "Default",
  "provider default": "Default",
  default: "Default",
  off: "Off",
  none: "Off",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  standard: "Standard",
  high: "High",
  xhigh: "XHigh",
  max: "Max",
  ultra: "Ultra",
};

/** Format a reasoning effort option label cleanly without bilingual duplication. */
export function formatThinkingLabel(
  raw: string | undefined,
  language: "zh-CN" | "en" = "zh-CN",
): string {
  if (!raw) return "";
  const cleaned = raw
    .trim()
    .replace(/^Thinking\s+/i, "")
    .trim();
  const key = cleaned.toLowerCase();
  const map = language === "en" ? enThinkingLabels : zhThinkingLabels;
  return Object.hasOwn(map, key) ? map[key] : cleaned;
}

/** Format a token count or context option string as compact K/M notation (e.g. 200K, 1M). */
export function formatContextTokens(value: number | string | undefined): string {
  if (value === undefined || value === "") return "";
  if (typeof value === "string") {
    const trimmed = value.trim();
    const compact = /^(\d+(?:\.\d+)?)\s*([km])\b/i.exec(trimmed);
    if (compact) return `${compact[1]}${compact[2].toUpperCase()}`;
    const numeric = Number(trimmed.replace(/[,_\s]/g, ""));
    if (!Number.isFinite(numeric) || numeric <= 0) return trimmed;
    value = numeric;
  }
  if (!Number.isFinite(value) || value <= 0) return "";
  const binary = value % 1024 === 0;
  const base = binary ? 1024 : 1000;
  if (value >= base * base) {
    const m = value / (base * base);
    return `${Number.isInteger(m) ? m : m.toFixed(1).replace(/\.0$/, "")}M`;
  }
  if (value >= base) {
    const k = value / base;
    return `${Number.isInteger(k) ? k : Math.round(k)}K`;
  }
  return String(value);
}

/** Infer context window badge from reported usage capacity or explicit model suffix (e.g. -256K). */
export function inferModelContextTokens(
  modelName: string | undefined,
  usageSize: number | undefined,
): string | undefined {
  if (usageSize && usageSize > 0) return formatContextTokens(usageSize);
  if (modelName) {
    const suffix = /(?:^|[-_\s])(\d+(?:\.\d+)?[km])(?:$|[-_\s])/i.exec(modelName);
    if (suffix) return suffix[1].toUpperCase();
  }
  return undefined;
}

export function reasoningConfigOption(
  transcript: Pick<Transcript, "configOptions">,
): ConfigOption | undefined {
  return transcript.configOptions.find(isReasoningOption);
}

export function contextWindowConfigOption(
  transcript: Pick<Transcript, "configOptions">,
): ConfigOption | undefined {
  return transcript.configOptions.find(isContextWindowOption);
}

export function nonModelConfigOptions(
  transcript: Pick<Transcript, "configOptions">,
): ConfigOption[] {
  return transcript.configOptions.filter(
    (option) => option.type === "select" && !isModelOption(option),
  );
}

export function sessionLabel(session: Pick<AgentSessionSummary, "title" | "agent_name">) {
  return session.title || session.agent_name;
}
