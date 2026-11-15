// Mirrors texsys.studio.files: same defaults, glob semantics and settings
// override, so every entry point applies one visibility contract.
export const DEFAULT_EXCLUDES: Record<string, boolean> = {
  "**/.git": true,
  "**/.svn": true,
  "**/.hg": true,
  "**/CVS": true,
  "**/.DS_Store": true,
  "**/Thumbs.db": true,
  "**/node_modules": true,
  "**/.venv": true,
  "**/venv": true,
  "**/__pycache__": true,
  "**/.pytest_cache": true,
  "**/.mypy_cache": true,
  "**/.ruff_cache": true,
};

const TOKENS = /"(?:[^"\\]|\\.)*"|\/\/[^\r\n]*|\/\*[\s\S]*?\*\//g;
const TRAILING_COMMA = /"(?:[^"\\]|\\.)*"|,(?=\s*[}\]])/g;

export function stripJsonc(text: string): string {
  return text
    .replace(TOKENS, (match) => (match.startsWith('"') ? match : match.replace(/[^\r\n]/g, " ")))
    .replace(TRAILING_COMMA, (match) => (match === "," ? " " : match));
}

function excludeRegex(pattern: string): RegExp {
  if (!pattern.includes("/")) pattern = "**/" + pattern;
  const parts: string[] = [];
  let index = 0;
  while (index < pattern.length) {
    const char = pattern[index];
    if (char === "*") {
      if (pattern.startsWith("**/", index)) {
        parts.push("(?:[^/]+/)*");
        index += 3;
        continue;
      }
      if (pattern.startsWith("**", index)) {
        parts.push(".*");
        index += 2;
        continue;
      }
      parts.push("[^/]*");
    } else if (char === "?") {
      parts.push("[^/]");
    } else {
      parts.push(char.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    }
    index += 1;
  }
  return new RegExp("^" + parts.join("") + "$");
}

export function compileExcludes(settingsText?: string): (relative: string) => boolean {
  const excludes = { ...DEFAULT_EXCLUDES };
  if (settingsText) {
    try {
      const data = JSON.parse(stripJsonc(settingsText));
      const overrides = data?.["files.exclude"];
      if (overrides && typeof overrides === "object")
        for (const [pattern, value] of Object.entries(overrides))
          if (typeof value === "boolean") excludes[pattern] = value;
    } catch {
      // Invalid settings keep the defaults, matching the server reader.
    }
  }
  const rules = Object.entries(excludes)
    .filter(([, hide]) => hide)
    .map(([pattern]) => excludeRegex(pattern));
  return (relative) => rules.some((rule) => rule.test(relative));
}

export async function readWorkspaceExcludes(
  root: FileSystemDirectoryHandle,
): Promise<(relative: string) => boolean> {
  try {
    const directory = await root.getDirectoryHandle(".vscode");
    const file = await directory.getFileHandle("settings.json");
    return compileExcludes(await (await file.getFile()).text());
  } catch {
    return compileExcludes();
  }
}
