import type { ProjectFile } from "./sdk";

export type FileListing = { files: ProjectFile[]; directories: string[]; truncated: boolean };
export type TreeEntry = { path: string; name: string; directory: boolean; depth: number };
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
  let lanes: string[] = [];
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
