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
  title: string;
  state: AgentSessionState;
  error: string;
  created_at: number;
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
export type AvailableCommand = { name: string; description?: string; input?: unknown };

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
  modes?: { currentModeId: string; availableModes: SessionMode[] };
  commands: AvailableCommand[];
  usage?: { used: number; size: number };
  changed: string[];
  /** Incremented when the agent changed project files and the workspace should refresh. */
  fileChanges: number;
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

/** Fold ordered session events into a new transcript without mutating the input. */
export function applyAgentEvents(
  previous: Transcript,
  events: AgentEvent[],
  root: string,
  reset = false,
): Transcript {
  const next: Transcript = reset
    ? emptyTranscript()
    : { ...previous, items: [...previous.items], terminals: { ...previous.terminals } };
  for (const event of events) {
    if (event.seq <= next.seq) continue;
    next.seq = event.seq;
    const seq = event.seq;
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
  const textContent = item.content
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

export function sessionLabel(session: Pick<AgentSessionSummary, "title" | "agent_name">) {
  return session.title || session.agent_name;
}
