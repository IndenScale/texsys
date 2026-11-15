export type OutlineRange = {
  startLine: number;
  startColumn: number;
  endLine: number;
  endColumn: number;
};

export type OutlineKind = "heading" | "object" | "array" | "string" | "number" | "boolean" | "null";

export type OutlineSymbol = {
  id: string;
  name: string;
  kind: OutlineKind;
  detail?: string;
  range: OutlineRange;
  span: OutlineRange;
  children: OutlineSymbol[];
};

export type OutlineSort = "position" | "name" | "category";

export function supportsOutline(language: string) {
  return language === "markdown" || language === "json";
}

export function outlineForDocument(language: string, text: string): OutlineSymbol[] {
  if (language === "markdown") return markdownOutline(text);
  if (language === "json") return jsonOutline(text);
  return [];
}

type MarkdownHeading = { level: number; name: string; line: number };

function markdownOutline(text: string): OutlineSymbol[] {
  const lines = text.split(/\r\n|\r|\n/);
  const headings: MarkdownHeading[] = [];
  let fence: { marker: string; length: number } | undefined;
  let paragraph = "";
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const fenceMatch = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (fenceMatch) {
      const marker = fenceMatch[1][0];
      if (!fence) fence = { marker, length: fenceMatch[1].length };
      else if (
        marker === fence.marker &&
        fenceMatch[1].length >= fence.length &&
        !fenceMatch[2].trim()
      )
        fence = undefined;
      paragraph = "";
      continue;
    }
    if (fence) {
      paragraph = "";
      continue;
    }
    const atx = /^ {0,3}(#{1,6})(?:[ \t]+(.*))?$/.exec(line);
    if (atx) {
      headings.push({
        level: atx[1].length,
        name: (atx[2] ?? "").replace(/[ \t]+#+[ \t]*$/, "").trim(),
        line: index + 1,
      });
      paragraph = "";
      continue;
    }
    const setext = /^ {0,3}(=+|-+)[ \t]*$/.exec(line);
    if (setext && paragraph) {
      headings.push({
        level: setext[1][0] === "=" ? 1 : 2,
        name: paragraph.trim(),
        line: index,
      });
      paragraph = "";
      continue;
    }
    paragraph = line.trim() ? line : "";
  }
  const roots: OutlineSymbol[] = [];
  const stack: { level: number; node: OutlineSymbol }[] = [];
  headings.forEach((heading, index) => {
    const next = headings.slice(index + 1).find((item) => item.level <= heading.level);
    const endLine = next ? next.line - 1 : lines.length;
    const node: OutlineSymbol = {
      id: `h${index}`,
      name: heading.name,
      kind: "heading",
      range: {
        startLine: heading.line,
        startColumn: 1,
        endLine: heading.line,
        endColumn: (lines[heading.line - 1]?.length ?? 0) + 1,
      },
      span: {
        startLine: heading.line,
        startColumn: 1,
        endLine,
        endColumn: (lines[endLine - 1]?.length ?? 0) + 1,
      },
      children: [],
    };
    while (stack.length && stack[stack.length - 1].level >= heading.level) stack.pop();
    const parent = stack[stack.length - 1];
    if (parent) parent.node.children.push(node);
    else roots.push(node);
    stack.push({ level: heading.level, node });
  });
  return roots;
}

type JsonNode = {
  kind: OutlineKind;
  name?: string;
  detail?: string;
  start: number;
  end: number;
  children: JsonNode[];
};

const preview = (value: string) => {
  const flat = value.replace(/\s+/g, " ");
  return flat.length > 60 ? `${flat.slice(0, 60)}…` : flat;
};

function jsonOutline(text: string): OutlineSymbol[] {
  const lineStarts: number[] = [0];
  for (let index = 0; index < text.length; index++)
    if (text[index] === "\n") lineStarts.push(index + 1);
  const positionAt = (offset: number) => {
    let low = 0;
    let high = lineStarts.length - 1;
    while (low < high) {
      const mid = (low + high + 1) >> 1;
      if (lineStarts[mid] <= offset) low = mid;
      else high = mid - 1;
    }
    return { line: low + 1, column: offset - lineStarts[low] + 1 };
  };
  let pos = 0;
  let failed = false;
  let budget = 20000;
  const trivia = () => {
    while (pos < text.length) {
      const ch = text[pos];
      if (ch === " " || ch === "\t" || ch === "\n" || ch === "\r") {
        pos++;
        continue;
      }
      if (ch === "/" && text[pos + 1] === "/") {
        while (pos < text.length && text[pos] !== "\n") pos++;
        continue;
      }
      if (ch === "/" && text[pos + 1] === "*") {
        pos += 2;
        while (pos < text.length && !(text[pos] === "*" && text[pos + 1] === "/")) pos++;
        pos = Math.min(text.length, pos + 2);
        continue;
      }
      break;
    }
  };
  const string = (): { value: string; start: number; end: number } | undefined => {
    const start = pos;
    if (text[pos] !== '"') {
      failed = true;
      return undefined;
    }
    pos++;
    let value = "";
    const simple: Record<string, string> = {
      '"': '"',
      "\\": "\\",
      "/": "/",
      b: "\b",
      f: "\f",
      n: "\n",
      r: "\r",
      t: "\t",
    };
    while (pos < text.length) {
      const ch = text[pos];
      if (ch === '"') {
        pos++;
        return { value, start, end: pos };
      }
      if (ch === "\n") {
        failed = true;
        return undefined;
      }
      if (ch === "\\") {
        const escape = text[pos + 1];
        if (escape === "u") {
          const hex = text.slice(pos + 2, pos + 6);
          if (!/^[0-9a-fA-F]{4}$/.test(hex)) {
            failed = true;
            return undefined;
          }
          value += String.fromCharCode(parseInt(hex, 16));
          pos += 6;
          continue;
        }
        if (!(escape in simple)) {
          failed = true;
          return undefined;
        }
        value += simple[escape];
        pos += 2;
        continue;
      }
      value += ch;
      pos++;
    }
    failed = true;
    return undefined;
  };
  const value = (): JsonNode | undefined => {
    if (failed || --budget < 0) {
      failed = true;
      return undefined;
    }
    trivia();
    const start = pos;
    const ch = text[pos];
    if (ch === "{" || ch === "[") {
      const object = ch === "{";
      const close = object ? "}" : "]";
      pos++;
      const children: JsonNode[] = [];
      trivia();
      if (text[pos] === close) {
        pos++;
        return { kind: object ? "object" : "array", start, end: pos, children };
      }
      for (;;) {
        if (failed) break;
        let entry: JsonNode | undefined;
        if (object) {
          trivia();
          const key = string();
          if (!key) break;
          trivia();
          if (text[pos] !== ":") {
            failed = true;
            break;
          }
          pos++;
          const item = value();
          if (!item) break;
          entry = { ...item, name: key.value, start: key.start };
        } else {
          const item = value();
          if (!item) break;
          entry = { ...item, name: String(children.length) };
        }
        children.push(entry);
        trivia();
        if (text[pos] === ",") {
          pos++;
          trivia();
          if (text[pos] === close) {
            pos++;
            break;
          }
          continue;
        }
        if (text[pos] === close) {
          pos++;
          break;
        }
        failed = true;
        break;
      }
      return { kind: object ? "object" : "array", start, end: pos, children };
    }
    if (ch === '"') {
      const parsed = string();
      if (!parsed) return undefined;
      return { kind: "string", detail: preview(parsed.value), start, end: pos, children: [] };
    }
    const literal = /^(true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(
      text.slice(pos, pos + 64),
    );
    if (literal) {
      pos += literal[0].length;
      const kind: OutlineKind =
        literal[0] === "true" || literal[0] === "false"
          ? "boolean"
          : literal[0] === "null"
            ? "null"
            : "number";
      return { kind, detail: literal[0], start, end: pos, children: [] };
    }
    failed = true;
    return undefined;
  };
  const root = value();
  const top = root && (root.kind === "object" || root.kind === "array") ? root.children : [];
  const convert = (node: JsonNode, id: string): OutlineSymbol => {
    const start = positionAt(node.start);
    const end = positionAt(Math.max(node.start, node.end - 1));
    const range: OutlineRange = {
      startLine: start.line,
      startColumn: start.column,
      endLine: end.line,
      endColumn: end.column + 1,
    };
    return {
      id,
      name: node.name ?? "",
      kind: node.kind,
      detail: node.detail,
      range,
      span: range,
      children: node.children.map((child, index) => convert(child, `${id}/${index}`)),
    };
  };
  return top.map((node, index) => convert(node, String(index)));
}

const kindOrder: Record<OutlineKind, number> = {
  object: 0,
  array: 1,
  string: 2,
  number: 3,
  boolean: 4,
  null: 5,
  heading: 6,
};

export function sortOutlineSymbols(nodes: OutlineSymbol[], mode: OutlineSort): OutlineSymbol[] {
  if (mode === "position") return nodes;
  const compare = (a: OutlineSymbol, b: OutlineSymbol) =>
    mode === "category"
      ? kindOrder[a.kind] - kindOrder[b.kind] || a.name.localeCompare(b.name, "zh-CN")
      : a.name.localeCompare(b.name, "zh-CN", { sensitivity: "base" }) ||
        a.name.localeCompare(b.name, "zh-CN");
  const walk = (list: OutlineSymbol[]): OutlineSymbol[] =>
    [...list].sort(compare).map((node) => ({ ...node, children: walk(node.children) }));
  return walk(nodes);
}

export function filterOutlineSymbols(nodes: OutlineSymbol[], query: string): OutlineSymbol[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return nodes;
  const walk = (list: OutlineSymbol[]): OutlineSymbol[] =>
    list.flatMap((node) => {
      const children = walk(node.children);
      return node.name.toLowerCase().includes(needle) || children.length
        ? [{ ...node, children }]
        : [];
    });
  return walk(nodes);
}

export function outlineSymbolAt(
  nodes: OutlineSymbol[],
  line: number,
  column: number,
): { id: string; ancestors: string[] } | undefined {
  const contains = (range: OutlineRange) =>
    (line > range.startLine || (line === range.startLine && column >= range.startColumn)) &&
    (line < range.endLine || (line === range.endLine && column <= range.endColumn));
  let result: { id: string; ancestors: string[] } | undefined;
  const walk = (list: OutlineSymbol[], ancestors: string[]) => {
    for (const node of list) {
      if (!contains(node.span)) continue;
      result = { id: node.id, ancestors };
      walk(node.children, [...ancestors, node.id]);
      return;
    }
  };
  walk(nodes, []);
  return result;
}
