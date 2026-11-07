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
