import type { SearchLine, SearchResults } from "./workspace-model";
export type SearchFileGroup = { path: string; count: number; lines: SearchLine[] };
/** A line is one result row, while counts retain every individual occurrence. */
export function groupCodeSearchResults(results?: SearchResults): SearchFileGroup[] {
  if (!results) return [];
  const lines = results.lines ?? [];
  if (!results.lines) {
    const fallback = new Map<string, SearchLine>();
    for (const match of results.matches) {
      const key = `${match.path}\0${match.line}`;
      let line = fallback.get(key);
      if (!line) {
        line = { path: match.path, line: match.line, text: match.text, count: 0, matches: [] };
        fallback.set(key, line);
      }
      line.count += 1;
      const start = results.matches.find(
        (item) => item.path === match.path && item.line === match.line,
      )!;
      const offset = match.column - start.column + start.offset;
      if (offset >= 0 && offset + match.length <= Array.from(line.text).length)
        line.matches.push({
          column: match.column,
          end_column: match.end_column,
          offset,
          length: match.length,
        });
    }
    lines.push(...fallback.values());
  }
  const groups = new Map<string, SearchFileGroup>();
  for (const line of lines) {
    let group = groups.get(line.path);
    if (!group) {
      group = { path: line.path, count: 0, lines: [] };
      groups.set(line.path, group);
    }
    group.lines.push(line);
    group.count += line.count;
  }
  return Array.from(groups.values());
}
export function searchLineSegments(line: SearchLine) {
  const characters = Array.from(line.text),
    segments: { text: string; match?: SearchLine["matches"][number] }[] = [];
  let end = 0;
  for (const match of line.matches) {
    if (match.offset > end) segments.push({ text: characters.slice(end, match.offset).join("") });
    segments.push({
      text: characters.slice(match.offset, match.offset + match.length).join(""),
      match,
    });
    end = match.offset + match.length;
  }
  if (end < characters.length) segments.push({ text: characters.slice(end).join("") });
  return segments;
}
