import { useEffect, useState } from "react";

type Snapshot<T> = { data?: T; error?: string };

export class ExtensionCache<T> {
  private entries = new Map<string, { data?: T; request: Promise<T>; expires: number }>();

  constructor(
    private limit: number,
    private lifetime: number,
  ) {}

  clear() {
    this.entries.clear();
  }

  read(key: string) {
    const entry = this.entries.get(key);
    return entry && entry.expires > Date.now() ? entry.data : undefined;
  }

  load(key: string, fetchData: () => Promise<T>): Promise<T> {
    const cached = this.entries.get(key);
    if (cached && cached.expires > Date.now()) {
      this.entries.delete(key);
      this.entries.set(key, cached);
      return cached.request;
    }
    if (this.entries.size >= this.limit && !this.entries.has(key)) {
      this.entries.delete(this.entries.keys().next().value!);
    }
    const entry = { data: undefined as T | undefined, expires: Infinity, request: fetchData() };
    this.entries.set(key, entry);
    void entry.request.then(
      (data) => {
        entry.data = data;
        entry.expires = Date.now() + this.lifetime;
      },
      () => {
        if (this.entries.get(key) === entry) this.entries.delete(key);
      },
    );
    return entry.request;
  }
}

export function useExtensionResource<T>(
  key: string | undefined,
  revision: number,
  cache: ExtensionCache<T>,
  load: (key: string) => Promise<T>,
): Snapshot<T> {
  const [state, setState] = useState<{ key: string; revision: number; result: Snapshot<T> }>();
  const cached = key ? cache.read(key) : undefined;
  useEffect(() => {
    if (!key) return;
    let current = true;
    void cache
      .load(key, () => load(key))
      .then(
        (data) => {
          if (current) setState({ key, revision, result: { data } });
        },
        (error) => {
          if (current)
            setState({
              key,
              revision,
              result: { error: error instanceof Error ? error.message : String(error) },
            });
        },
      );
    return () => {
      current = false;
    };
  }, [key, revision, cache, load]);

  if (cached !== undefined) return { data: cached };
  return state && state.key === key && state.revision === revision ? state.result : {};
}

export type ExtensionMarkdown = { content: string; truncated: boolean };

export async function loadExtensionMarkdown(url: string): Promise<ExtensionMarkdown> {
  const response = await fetch(url, {
    signal: AbortSignal.timeout(15_000),
    credentials: "omit",
    referrerPolicy: "no-referrer",
  });
  if (!response.ok || !response.body) throw new Error();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let remaining = 256 * 1024;
  let content = "";
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) return { content: content + decoder.decode(), truncated: false };
    if (chunk.value.byteLength > remaining) {
      content += decoder.decode(chunk.value.subarray(0, remaining), { stream: true });
      void reader.cancel().catch(() => {});
      // Prefer a complete block near the boundary, leaving the opening text useful.
      const boundary = content.lastIndexOf("\n\n");
      return {
        content: boundary > content.length - 4096 ? content.slice(0, boundary) : content,
        truncated: true,
      };
    }
    remaining -= chunk.value.byteLength;
    content += decoder.decode(chunk.value, { stream: true });
  }
}
