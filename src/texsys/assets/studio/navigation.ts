/** Document history is independent from browser URLs and Activity selection. */
export type DocumentHistory<T extends string = string> = { entries: T[]; index: number };
export function visitDocument<T extends string>(
  history: DocumentHistory<T>,
  view: T,
): DocumentHistory<T> {
  if (history.entries[history.index] === view) return history;
  return {
    entries: [...history.entries.slice(0, history.index + 1), view],
    index: history.index + 1,
  };
}
export function stepDocument<T extends string>(
  history: DocumentHistory<T>,
  step: -1 | 1,
): DocumentHistory<T> {
  const index = history.index + step;
  return index < 0 || index >= history.entries.length ? history : { ...history, index };
}

export function closeDocumentFromList<T extends string>(
  documents: T[],
  target: T,
): { remaining: T[]; next: T | null } {
  const index = documents.indexOf(target);
  const remaining = documents.filter((item) => item !== target);
  const next =
    remaining.length > 0 ? (remaining[index] ?? remaining[index - 1] ?? remaining[0]) : null;
  return { remaining, next };
}
