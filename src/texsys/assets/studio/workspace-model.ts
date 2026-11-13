import type { TextFormat, TextFormatChange } from "./sdk";
import type { FilePreview, ProjectFile } from "./sdk";

export function sourceFileFormat(path: string): string {
  const name = path.slice(path.lastIndexOf("/") + 1).toLowerCase();
  return name === "texsys.lock" ? "toml" : (name.split(".").pop() ?? "");
}

export type FileListing = { files: ProjectFile[]; directories: string[]; truncated: boolean };
export type TreeEntry = {
  path: string;
  name: string;
  detail?: string;
  directory: boolean;
  depth: number;
  icon?: string;
  virtual?: boolean;
  opensDocument?: boolean;
};
export function explorerCreationParent(listing: FileListing, selected: string): string {
  if (listing.directories.includes(selected)) return selected;
  if (listing.files.some((file) => file.path === selected))
    return selected.split("/").slice(0, -1).join("/");
  return "";
}

/** Insert a draft among direct children without splitting an expanded folder's subtree. */
export function explorerCreationIndex(entries: TreeEntry[], parent: string, directory: boolean) {
  const start = parent ? entries.findIndex((entry) => entry.path === parent) + 1 : 0;
  if (parent && start === 0) return -1;
  const depth = parent ? parent.split("/").length : 0;
  if (directory) return start;
  let index = start;
  while (index < entries.length) {
    const entry = entries[index];
    if (entry.depth < depth || (entry.depth === depth && !entry.directory)) break;
    index += 1;
  }
  return index;
}

export function visibleFileTree(listing: FileListing, expanded: Set<string>): TreeEntry[] {
  const children = new Map<string, TreeEntry[]>();
  const add = (path: string, directory: boolean) => {
    const parts = path.split("/"),
      name = parts.pop()!,
      parent = parts.join("/");
    const items = children.get(parent) ?? [];
    items.push({ path, name, directory, depth: parts.length });
    children.set(parent, items);
  };
  const folders = new Set(listing.directories);
  for (const file of listing.files) {
    const parts = file.path.split("/");
    parts.pop();
    while (parts.length) {
      folders.add(parts.join("/"));
      parts.pop();
    }
  }
  folders.forEach((path) => add(path, true));
  listing.files.forEach((file) => add(file.path, false));
  const result: TreeEntry[] = [];
  const visit = (parent: string) => {
    const items = children.get(parent) ?? [];
    items.sort((a, b) => Number(b.directory) - Number(a.directory) || a.name.localeCompare(b.name));
    for (const entry of items) {
      result.push(entry);
      if (entry.directory && expanded.has(entry.path)) visit(entry.path);
    }
  };
  visit("");
  return result;
}
export type SearchMatch = {
  path: string;
  line: number;
  column: number;
  end_column: number;
  text: string;
  offset: number;
  length: number;
};
export type SearchLine = {
  path: string;
  line: number;
  text: string;
  prefix?: boolean;
  suffix?: boolean;
  count: number;
  matches: { column: number; end_column: number; offset: number; length: number }[];
};
export type SearchResults = {
  lines?: SearchLine[];
  matches: SearchMatch[];
  scanned: number;
  skipped: number;
  truncated: boolean;
};
export type GitEntry = { path: string; original?: string; index: string; worktree: string };
export type GitStatus = {
  available: boolean;
  reason?: string;
  repository?: string;
  project_prefix?: string;
  scope?: string;
  branch?: string;
  detached?: boolean;
  entries: GitEntry[];
  snapshot: string;
  truncated?: boolean;
};
export type GitDiff = {
  path: string;
  staged: boolean;
  content: string;
  truncated: boolean;
  title?: string;
  historical?: boolean;
};
export function gitPathInProject(path: string, projectPrefix?: string): string | undefined {
  if (!projectPrefix) return path;
  const directory = projectPrefix + "/";
  return path.startsWith(directory) ? path.slice(directory.length) : undefined;
}

export function gitGroups(status?: GitStatus) {
  const entries = status?.entries ?? [];
  return {
    staged: entries.filter((entry) => ![" ", "?"].includes(entry.index)),
    changes: entries.filter((entry) => entry.worktree !== " "),
  };
}
/** Editor offsets use UTF-16; backend search columns count Unicode code points. */
export function sourceOffset(content: string, line: number, column: number): number {
  const lines = content.split("\n");
  const index = Math.min(Math.max(0, line - 1), lines.length - 1);
  return (
    lines.slice(0, index).reduce((sum, value) => sum + value.length + 1, 0) +
    Array.from(lines[index].replace(/\r$/, ""))
      .slice(0, Math.max(0, column - 1))
      .join("").length
  );
}

export type GitCommit = {
  id: string;
  parents: string[];
  subject: string;
  author: string;
  date: string;
  refs: string[];
};
export type GitHistory = { commits: GitCommit[]; truncated: boolean };
/** Keep each pending parent in a lane, joining lanes when branches merge. */
export function commitGraph(commits: GitCommit[]) {
  const lanes: string[] = [];
  return commits.map((commit) => {
    if (!lanes.includes(commit.id)) lanes.push(commit.id);
    const before = [...lanes];
    const lane = lanes.indexOf(commit.id);
    lanes.splice(lane, 1);
    commit.parents.forEach((parent, index) => {
      if (!lanes.includes(parent)) lanes.splice(Math.min(lane + index, lanes.length), 0, parent);
    });
    return { commit, lane, before, after: [...lanes] };
  });
}

/** All readers of one document share the latest request, including deferred state updates. */
export class FileReadRequests {
  private latest = new Map<
    string,
    { token: symbol; controller: AbortController; pending: boolean }
  >();

  begin(path: string) {
    const token = Symbol(path);
    this.invalidate(path);
    const request = { token, controller: new AbortController(), pending: true };
    this.latest.set(path, request);
    const isCurrent = () => this.latest.get(path)?.token === token;
    return {
      isCurrent,
      signal: request.controller.signal,
      finish: () => {
        request.pending = false;
      },
      update: <T>(previous: T, apply: (previous: T) => T) =>
        isCurrent() ? apply(previous) : previous,
      cancel: () => {
        if (isCurrent()) this.invalidate(path);
      },
    };
  }

  pending(path: string) {
    return this.latest.get(path)?.pending === true;
  }

  /** A refresh never displaces an unfinished open, reload, or another refresh. */
  beginRefresh(path: string) {
    return this.pending(path) ? undefined : this.begin(path);
  }

  // Keep completed tokens until superseded or closed: React may apply their updates later.
  invalidate(path: string) {
    this.latest.get(path)?.controller.abort();
    this.latest.delete(path);
  }
}

export function filePreviewKind(file?: Pick<FilePreview, "path" | "preview" | "content">) {
  if (!file) return;
  if (/\.(md|markdown)$/i.test(file.path)) return "markdown";
  if (/\.svg$/i.test(file.path)) return "svg";
  if (file.preview?.kind === "scene" || /\.usd[ac]?$/i.test(file.path)) return "scene";
  if (["diagram", "schematic", "topology"].includes(file.preview?.kind ?? "")) return "diagram";
  if (/\.json$/i.test(file.path) && file.content) {
    try {
      const data = JSON.parse(file.content);
      if (
        data?.schema_version === 1 &&
        ["system", "schematic"].includes(data.view) &&
        ["system-diagram", "schematic"].includes(data.deliverable) &&
        data.backend?.protocol === 1 &&
        data.view_contract &&
        typeof data.view_contract === "object" &&
        Array.isArray(data.pages) &&
        Array.isArray(data.nodes) &&
        Array.isArray(data.edges)
      )
        return "diagram";
    } catch {
      // Invalid JSON remains editable as source; declared diagrams still show their parse error.
    }
  }
}
export function fileDocumentSnapshot(path: string, file: FilePreview) {
  if (file.path !== path) throw new Error("File response does not match the requested path.");
  return {
    saved: file.content ?? "",
    content: file.content ?? "",
    version: file.version ?? "",
    ...(file.textFormat ? { textFormat: file.textFormat, formatChange: undefined } : {}),
    reason: file.reason,
    generated: !!file.generated,
    preview: file.preview,
    loading: false,
    error: undefined,
  };
}
type FileDocumentState = {
  path: string;
  content: string;
  saved: string;
  version: string;
  textFormat?: TextFormat;
  formatChange?: TextFormatChange;
};
export function isDocumentDirty(
  document: Pick<FileDocumentState, "content" | "saved" | "formatChange">,
) {
  return document.content !== document.saved || !!document.formatChange;
}
export function editorTextFormat(
  document: Pick<FileDocumentState, "textFormat" | "formatChange">,
): TextFormat {
  return { encoding: "utf8", lineEnding: "LF", ...document.textFormat, ...document.formatChange };
}
/** Background reads cannot replace edits or a newer disk baseline. */
export function refreshedFileDocument<T extends FileDocumentState>(
  document: T,
  file: FilePreview,
  expectedVersion: string,
): T {
  if (isDocumentDirty(document) || document.version !== expectedVersion) return document;
  return { ...document, ...fileDocumentSnapshot(document.path, file) };
}
/** A save acknowledges its submitted bytes while preserving typing after the request began. */
export function savedFileDocument<T extends FileDocumentState>(document: T, file: FilePreview): T {
  return { ...document, ...fileDocumentSnapshot(document.path, file), content: document.content };
}
/** Use the shortest unique trailing path so same-named generated files keep distinct tabs. */
export function fileDocumentTitle(path: string, openPaths: string[]) {
  const parts = path.split("/");
  let count = 1;
  while (
    count < parts.length &&
    openPaths.some(
      (other) =>
        other !== path &&
        other.split("/").slice(-count).join("/") === parts.slice(-count).join("/"),
    )
  )
    count += 1;
  return count === 1 ? parts.at(-1)! : `${parts.at(-1)} · ${parts.slice(-count, -1).join("/")}`;
}
